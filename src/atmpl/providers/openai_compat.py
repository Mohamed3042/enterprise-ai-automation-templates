"""Any endpoint that speaks OpenAI's `/chat/completions`.

One adapter covers OpenAI, Azure OpenAI, OpenRouter, Together, vLLM and a local Ollama —
they differ by base URL, key and model name, which are settings, not code.

**Contract-tested, not live-verified**: no `OPENAI_API_KEY` exists on the machine this was
built on, so the request shape, response parsing and error mapping are pinned to recorded
pairs in `tests/cassettes/openai/` authored from the documented schema.
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


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout: float = 30.0,
        name: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if name:
            self.name = name
        self._api_key = api_key if api_key is not None else os.getenv(api_key_env)

    def configured(self) -> bool:
        return bool(self._api_key)

    def build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "atmpl_structured_output",
                    "schema": schema,
                    "strict": False,
                },
            }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        return body

    def parse_response(self, body: dict[str, Any], latency_ms: float) -> ProviderResult:
        choices = body.get("choices") or []
        if not choices:
            raise ProviderError("malformed_response", "no choices returned", provider=self.name)
        message = choices[0].get("message") or {}
        text = message.get("content")
        raw_calls = message.get("tool_calls") or []
        calls: list[ToolCall] = []
        for entry in raw_calls:
            function = entry.get("function") or {}
            raw_arguments = function.get("arguments")
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else {}
            except ValueError as exc:
                raise ProviderError(
                    "malformed_response",
                    f"tool call arguments were not JSON: {exc}",
                    provider=self.name,
                ) from exc
            calls.append(ToolCall(str(function.get("name")), dict(arguments)))
        usage_raw = body.get("usage") or {}
        return ProviderResult(
            provider=self.name,
            model=str(body.get("model") or self.model),
            latency_ms=latency_ms,
            text=text,
            json=_maybe_json(text),
            tool_calls=tuple(calls),
            usage=Usage(
                input_tokens=usage_raw.get("prompt_tokens"),
                output_tokens=usage_raw.get("completion_tokens"),
            ),
            finish_reason=choices[0].get("finish_reason"),
        )

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
                "No API key is set for the OpenAI-compatible endpoint.",
                provider=self.name,
            )
        body = self.build_request(
            messages,
            tools=tools,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={
                    "authorization": f"Bearer {self._api_key}",
                    "content-type": "application/json",
                },
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
