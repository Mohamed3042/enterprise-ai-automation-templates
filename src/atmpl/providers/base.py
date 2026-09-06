"""One interface every model provider implements, and one shape every answer comes back in.

The interface is deliberately small: messages in, and either text, a JSON object matching a
schema, or tool calls out — plus the usage and latency the LLMOps page needs. Anything a
particular vendor does beyond that is the adapter's problem, not the caller's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]

#: Error kinds the router reacts to. `retryable` is a property of the kind, not the vendor.
ErrorKind = Literal[
    "rate_limited",
    "server_error",
    "timeout",
    "network",
    "bad_request",
    "auth",
    "malformed_response",
    "not_configured",
]

RETRYABLE_KINDS: frozenset[str] = frozenset({"rate_limited", "server_error", "timeout", "network"})


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class ToolSpec:
    """A callable the model may request, described by a JSON Schema for its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def measured(self) -> bool:
        """False when the provider reported nothing — which is unknown, never zero."""
        return self.input_tokens is not None or self.output_tokens is not None


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str
    latency_ms: float
    text: str | None = None
    json: dict[str, Any] | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    #: Set by the router when the primary provider failed and this one answered instead.
    fallback_from: str | None = None
    attempt: int = 1


class ProviderError(RuntimeError):
    """A failed provider call, classified so the router can decide what to do next."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"{provider}: {message}")
        self.kind = kind
        self.provider = provider
        self.status_code = status_code
        self.detail = message

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS


class Provider(Protocol):
    """What the router needs from any model backend."""

    name: str
    model: str

    def configured(self) -> bool:
        """True when this provider has everything it needs to make a real call."""
        ...

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResult: ...


def status_to_kind(status_code: int) -> ErrorKind:
    if status_code == 401 or status_code == 403:
        return "auth"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "server_error"
    return "bad_request"


def render_prompt(messages: list[Message]) -> str:
    """A single prompt string for backends without a role-tagged message array."""
    return "\n\n".join(f"[{message.role}]\n{message.content}" for message in messages)
