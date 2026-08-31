"""Adapter interface and fail-closed configuration selection."""

from __future__ import annotations

import os
from typing import Any, Protocol

from atmpl.models import LLMOutput


class LLMAdapter(Protocol):
    name: str

    def generate(self, task: str, payload: dict[str, Any]) -> LLMOutput: ...


def create_adapter(name: str | None = None) -> LLMAdapter:
    selected = name or os.getenv("ATMPL_LLM_ADAPTER", "mock")
    if selected == "mock":
        from atmpl.adapters.mock import MockAdapter

        return MockAdapter()
    if selected == "claude":
        from atmpl.adapters.claude import ClaudeAdapter

        return ClaudeAdapter()
    raise ValueError(f"Unknown LLM adapter '{selected}'. Allowed: mock, claude")

