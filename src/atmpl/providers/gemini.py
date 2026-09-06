"""Google Generative Language API (Gemini) over plain HTTP.

Live-verified against `gemini-3.6-flash` from this repository. Structured output uses the
API's own `responseSchema`, so a JSON answer is the model's contract rather than a
post-hoc parse of prose.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from atmpl.providers.base import (
    Message,
    ProviderError,
    ProviderResult,
    ToolCall,
    ToolSpec,
    Usage,
    status_to_kind,
)

#: `responseSchema` accepts an OpenAPI-3.0 subset, not full JSON Schema. Anything else is
#: rejected with a 400, so unsupported keywords are stripped and `$ref`s are inlined.
_ALLOWED_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "propertyOrdering",
        "minItems",
        "maxItems",
    }
)


def to_api_schema(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reduce a Pydantic JSON Schema to the subset Gemini's `responseSchema` accepts."""
    defs = defs if defs is not None else (schema.get("$defs") or {})
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        return to_api_schema(dict(defs.get(name, {})), defs)
    if "anyOf" in schema:
        # Optional[...] renders as anyOf[T, null]; keep the first non-null branch.
        for option in schema["anyOf"]:
            if option.get("type") != "null":
                reduced = to_api_schema(dict(option), defs)
                reduced["nullable"] = True
                return reduced
    reduced: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            reduced[key] = {name: to_api_schema(dict(item), defs) for name, item in value.items()}
        elif key == "items" and isinstance(value, dict):
            reduced[key] = to_api_schema(dict(value), defs)
        else:
            reduced[key] = value
    reduced.setdefault("type", "object" if "properties" in reduced else "string")
    return reduced


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        model: str = "gemini-3.6-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")

    def configured(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------ wire shape

    def build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            role = "model" if message.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": message.content}]})
        body: dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}]}
        if system_parts:
            body["system_instruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        generation: dict[str, Any] = {}
        if schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = to_api_schema(schema)
        if temperature is not None:
            generation["temperature"] = temperature
        if max_tokens is not None:
            generation["maxOutputTokens"] = max_tokens
        if generation:
            body["generationConfig"] = generation
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": to_api_schema(tool.parameters),
                        }
                        for tool in tools
                    ]
                }
            ]
        return body

    def parse_response(self, body: dict[str, Any], latency_ms: float) -> ProviderResult:
        candidates = body.get("candidates") or []
        if not candidates:
            raise ProviderError(
                "malformed_response",
                f"no candidates in response (finish: {body.get('promptFeedback')})",
                provider=self.name,
            )
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        texts = [part["text"] for part in parts if "text" in part]
        calls = tuple(
            ToolCall(part["functionCall"]["name"], dict(part["functionCall"].get("args") or {}))
            for part in parts
            if "functionCall" in part
        )
        text = "".join(texts) or None
        return ProviderResult(
            provider=self.name,
            model=self.model,
            latency_ms=latency_ms,
            text=text,
            json=_maybe_json(text),
            tool_calls=calls,
            usage=_usage(body.get("usageMetadata") or {}),
            finish_reason=candidate.get("finishReason"),
        )

    # ------------------------------------------------------------------ call

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResult:
        if not self.configured():
            raise ProviderError(
                "not_configured",
                "GEMINI_API_KEY is not set.",
                provider=self.name,
            )
        body = self.build_request(
            messages,
            tools=tools,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        url = f"{self.base_url}/models/{self.model}:generateContent"
        started = time.perf_counter()
        try:
            response = httpx.post(
                url,
                json=body,
                headers={"x-goog-api-key": self._api_key or "", "content-type": "application/json"},
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", str(exc), provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network", str(exc), provider=self.name) from exc
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            raise ProviderError(
                status_to_kind(response.status_code),
                _error_message(response),
                provider=self.name,
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("malformed_response", str(exc), provider=self.name) from exc
        return self.parse_response(payload, latency_ms)


def _usage(raw: dict[str, Any]) -> Usage:
    """Answer tokens plus reasoning tokens: Gemini bills `thoughtsTokenCount` as output.

    Counting only `candidatesTokenCount` under-reports a thinking model's cost by an order
    of magnitude — measured on `gemini-3.6-flash`: 1 answer token, 88 thought tokens.
    """
    answer = raw.get("candidatesTokenCount")
    thoughts = raw.get("thoughtsTokenCount")
    unreported = answer is None and thoughts is None
    output = None if unreported else (answer or 0) + (thoughts or 0)
    return Usage(input_tokens=raw.get("promptTokenCount"), output_tokens=output)


def _maybe_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", error))[:300]
    return str(payload)[:300]
