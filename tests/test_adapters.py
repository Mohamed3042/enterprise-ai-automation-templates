import pytest

from atmpl.adapters.base import create_adapter
from atmpl.adapters.mock import MockAdapter


def test_mock_adapter_is_deterministic():
    adapter = MockAdapter()
    first = adapter.generate("loan_extract", {"synthetic": True, "amount": 1200})
    second = adapter.generate("loan_extract", {"amount": 1200, "synthetic": True})

    assert first == second
    assert first.adapter == "mock"
    assert first.content["recommendation"] == "REFER_TO_TIER_2"


def test_mock_is_default_without_api_key(monkeypatch):
    monkeypatch.delenv("ATMPL_LLM_ADAPTER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert isinstance(create_adapter(), MockAdapter)


def test_claude_adapter_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        create_adapter("claude")

