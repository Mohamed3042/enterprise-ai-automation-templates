"""DEPRECATED alias of :mod:`atmpl.providers`. Kept so v0.1 imports keep working.

`create_adapter` still fails closed: naming a provider whose key is absent raises rather
than quietly falling back to the mock. What changed in v0.3 is what it returns — a
:class:`~atmpl.providers.adapter.RouterAdapter` over the provider router, which adds
retries, a fallback chain, spans, metrics and a cost estimate to the same `generate` call.
See `docs/adr/0007-provider-router.md`.
"""

from __future__ import annotations

from typing import Any, Protocol

from atmpl.models import LLMOutput
from atmpl.providers.adapter import RouterAdapter
from atmpl.providers.router import KNOWN_PROVIDERS, PROVIDER_ALIASES, ProviderRouter, build_provider

#: Which environment variable a caller has to set for each hosted provider.
KEY_VARIABLES = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class LLMAdapter(Protocol):
    name: str

    def generate(
        self,
        task: str,
        payload: dict[str, Any],
        *,
        output_schema: dict[str, Any] | None = None,
        template_key: str | None = None,
        organization: str | None = None,
    ) -> LLMOutput: ...


def create_adapter(
    name: str | None = None,
    *,
    settings=None,
    observability=None,
) -> LLMAdapter:
    """Build the configured adapter, or raise if the named provider cannot run."""
    from atmpl.settings import settings_from_env

    settings = settings or settings_from_env()
    if name:
        settings = settings.model_copy(update={"adapter": name, "provider_fallbacks": []})
    chain = settings.provider_chain or ["mock"]
    primary = build_provider(chain[0], settings)
    if not primary.configured():
        resolved = PROVIDER_ALIASES.get(primary.name, primary.name)
        variable = KEY_VARIABLES.get(resolved, "its API key")
        raise RuntimeError(
            f"The '{chain[0]}' provider requires {variable}; "
            "use the offline mock provider by default."
        )
    router = ProviderRouter(settings, observability=observability)
    return RouterAdapter(router)


__all__ = ["KEY_VARIABLES", "KNOWN_PROVIDERS", "LLMAdapter", "create_adapter"]
