"""Parse a recorded provider response body with the shipped adapter code.

The contract tests in `tests/cassettes/` and the `provider_response` eval probe both go
through here, so a cassette can never be checked against a parser that only exists in a
test file.
"""

from __future__ import annotations

from typing import Any

from atmpl.providers.anthropic import AnthropicProvider
from atmpl.providers.base import ProviderResult
from atmpl.providers.gemini import GeminiProvider
from atmpl.providers.openai_compat import OpenAICompatibleProvider

PARSERS = {
    "gemini": lambda: GeminiProvider(api_key="recorded"),
    "anthropic": lambda: AnthropicProvider(api_key="recorded"),
    "openai": lambda: OpenAICompatibleProvider(api_key="recorded"),
}


def parse_recorded(provider: str, body: dict[str, Any], latency_ms: float = 0.0) -> ProviderResult:
    factory = PARSERS.get(provider)
    if factory is None:
        raise KeyError(f"No recorded parser for provider '{provider}'")
    return factory().parse_response(body, latency_ms)
