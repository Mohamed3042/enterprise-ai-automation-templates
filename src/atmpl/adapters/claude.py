"""DEPRECATED alias. The Anthropic adapter lives in :mod:`atmpl.providers.anthropic`.

``ClaudeAdapter`` remains importable and still fails closed without ``ANTHROPIC_API_KEY``;
it now routes through :class:`~atmpl.providers.router.ProviderRouter`, so it inherits the
retries, the timeout, the span and the cost estimate the v0.1 class did not have.
"""

from __future__ import annotations

from typing import Any

from atmpl.models import LLMOutput
from atmpl.providers.adapter import RouterAdapter
from atmpl.providers.anthropic import AnthropicProvider
from atmpl.providers.router import ProviderRouter
from atmpl.settings import settings_from_env


class ClaudeAdapter:
    name = "anthropic"

    def __init__(self, settings=None, observability=None) -> None:
        settings = settings or settings_from_env()
        provider = AnthropicProvider(
            model=settings.anthropic_model,
            base_url=settings.anthropic_base_url,
            timeout=settings.provider_timeout_seconds,
        )
        if not provider.configured():
            raise RuntimeError(
                "Claude adapter requires ANTHROPIC_API_KEY; "
                "use the offline mock adapter by default."
            )
        self.model = provider.model
        self._adapter = RouterAdapter(
            ProviderRouter(settings, observability=observability, providers=[provider])
        )

    def generate(
        self,
        task: str,
        payload: dict[str, Any],
        *,
        output_schema: dict[str, Any] | None = None,
        template_key: str | None = None,
        organization: str | None = None,
    ) -> LLMOutput:
        return self._adapter.generate(
            task,
            payload,
            output_schema=output_schema,
            template_key=template_key,
            organization=organization,
        )


__all__ = ["ClaudeAdapter"]
