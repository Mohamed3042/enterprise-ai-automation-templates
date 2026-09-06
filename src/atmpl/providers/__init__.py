"""Model providers behind one interface, and the router that chooses between them.

`mock` stays the keyless default. Gemini is live-verified from this repository; the
Anthropic and OpenAI-compatible adapters are contract-tested against recorded responses,
because no key for either exists on the machine this was built on — the README says the
same thing in the same words.
"""

from __future__ import annotations

from atmpl.providers.adapter import RouterAdapter, build_messages, schema_from_stage
from atmpl.providers.anthropic import AnthropicProvider
from atmpl.providers.base import (
    Message,
    Provider,
    ProviderError,
    ProviderResult,
    ToolCall,
    ToolSpec,
    Usage,
)
from atmpl.providers.gemini import GeminiProvider
from atmpl.providers.mock import MockProvider
from atmpl.providers.openai_compat import OpenAICompatibleProvider
from atmpl.providers.pricing import PriceTable, load_prices
from atmpl.providers.router import (
    KNOWN_PROVIDERS,
    PROVIDER_ALIASES,
    AllProvidersFailed,
    CircuitBreaker,
    ProviderRouter,
    ProviderStatus,
    build_provider,
)

__all__ = [
    "KNOWN_PROVIDERS",
    "PROVIDER_ALIASES",
    "AllProvidersFailed",
    "AnthropicProvider",
    "CircuitBreaker",
    "GeminiProvider",
    "Message",
    "MockProvider",
    "OpenAICompatibleProvider",
    "PriceTable",
    "Provider",
    "ProviderError",
    "ProviderResult",
    "ProviderRouter",
    "ProviderStatus",
    "RouterAdapter",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "build_messages",
    "build_provider",
    "load_prices",
    "schema_from_stage",
]
