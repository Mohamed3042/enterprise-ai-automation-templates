"""DEPRECATED alias. The deterministic provider now lives in :mod:`atmpl.providers.mock`.

``MockAdapter`` is kept because v0.1 code imported it directly; it produces byte-identical
output to the v0.1 class, which is why the seeded demos' audit hashes did not move.
"""

from __future__ import annotations

from typing import Any

from atmpl.models import LLMOutput
from atmpl.providers.mock import draft_content, input_hash


class MockAdapter:
    """Deterministic offline adapter. Prefer ``atmpl.providers.MockProvider``."""

    name = "mock"

    def generate(
        self,
        task: str,
        payload: dict[str, Any],
        *,
        output_schema: dict[str, Any] | None = None,
        template_key: str | None = None,
        organization: str | None = None,
    ) -> LLMOutput:
        return LLMOutput(
            task=task,
            input_hash=input_hash(task, payload),
            content=draft_content(task),
            adapter=self.name,
        )


__all__ = ["MockAdapter"]
