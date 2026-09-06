"""Anthropic Messages API.

**Contract-tested, not live-verified.** No `ANTHROPIC_API_KEY` exists on the machine this
was built on, so the request shape, the response parsing and the error mapping are tested
against recorded request/response pairs in `tests/cassettes/anthropic/`, authored from the
documented API schema. The README says so in the same words.

Structured output uses the documented forced-tool pattern: the JSON Schema becomes a tool's
`input_schema` and `tool_choice` names it, so the model must answer in that shape.
"""

from __future__ import annotations

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

STRUCTURED_TOOL_NAME = "emit_structured_output"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-5",
        base_url: str = "https://api.anthropic.com/v1",
        api_key: str | None = None,
        timeout: float = 30.0,
        api_version: str = "2023-06-01",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_version = api_version
        self._api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY")

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
        system_parts = [m.content for m in messages if m.role == "system"]
        conversation = [
            {
                "role": "assistant" if message.role == "assistant" else "user",
                "content": message.content,
            }
            for message in messages
            if message.role != "system"
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or 2048,
            "messages": conversation or [{"role": "user", "content": ""}],
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if temperature is not None:
            body["temperature"] = temperature
        declared = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools or ()
        ]
        if schema is not None:
            declared.append(
                {
                    "name": STRUCTURED_TOOL_NAME,
                    "description": "Return the answer as one object matching this schema.",
                    "input_schema": schema,
                }
            )
            if not tools:
                body["tool_choice"] = {"type": "tool", "name": STRUCTURED_TOOL_NAME}
        if declared:
            body["tools"] = declared
        return body

    def parse_response(self, body: dict[str, Any], latency_ms: float) -> ProviderResult:
        blocks = body.get("content")
        if not isinstance(blocks, list):
            raise ProviderError(
                "malformed_response", "response has no content blocks", provider=self.name
            )
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        structured = next(
            (dict(b.get("input") or {}) for b in uses if b.get("name") == STRUCTURED_TOOL_NAME),
            None,
        )
        calls = tuple(
            ToolCall(str(b.get("name")), dict(b.get("input") or {}))
            for b in uses
            if b.get("name") != STRUCTURED_TOOL_NAME
        )
        usage_raw = body.get("usage") or {}
        return ProviderResult(
            provider=self.name,
            model=str(body.get("model") or self.model),
            latency_ms=latency_ms,
            text="".join(texts) or None,
            json=structured,
            tool_calls=calls,
            usage=Usage(
                input_tokens=usage_raw.get("input_tokens"),
                output_tokens=usage_raw.get("output_tokens"),
            ),
            finish_reason=body.get("stop_reason"),
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
                "not_configured", "ANTHROPIC_API_KEY is not set.", provider=self.name
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
                f"{self.base_url}/messages",
                json=body,
                headers={
                    "x-api-key": self._api_key or "",
                    "anthropic-version": self.api_version,
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


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", error))[:300]
    return str(payload)[:300]
