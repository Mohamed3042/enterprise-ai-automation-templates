"""The provider boundary: what is default, what fails closed, what stayed importable."""

import pytest

from atmpl.adapters.base import create_adapter
from atmpl.adapters.mock import MockAdapter
from atmpl.providers import MockProvider, RouterAdapter
from atmpl.providers.mock import draft_content


def test_mock_adapter_is_deterministic():
    adapter = MockAdapter()
    first = adapter.generate("loan_extract", {"synthetic": True, "amount": 1200})
    second = adapter.generate("loan_extract", {"amount": 1200, "synthetic": True})

    assert first == second
    assert first.adapter == "mock"
    assert first.content["recommendation"] == "REFER_TO_TIER_2"


def test_mock_is_default_without_api_key(monkeypatch):
    """v0.3 returns a router-backed adapter; the provider behind it is still the mock."""
    monkeypatch.delenv("ATMPL_LLM_ADAPTER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    adapter = create_adapter()

    assert isinstance(adapter, RouterAdapter)
    assert adapter.name == "mock"
    assert isinstance(adapter.router.primary, MockProvider)


def test_router_adapter_reproduces_the_v01_mock_content(monkeypatch):
    """The seeded demos' audit hashes depend on this staying byte-identical."""
    monkeypatch.delenv("ATMPL_LLM_ADAPTER", raising=False)
    payload = {"synthetic": True, "amount": 1200}

    legacy = MockAdapter().generate("loan_extract", payload)
    routed = create_adapter().generate("loan_extract", payload)

    assert routed.content == legacy.content == draft_content("loan_extract")
    assert routed.input_hash == legacy.input_hash
    assert routed.adapter == legacy.adapter == "mock"


@pytest.mark.parametrize(
    ("name", "variable"),
    [
        ("claude", "ANTHROPIC_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
    ],
)
def test_hosted_adapters_fail_closed_without_their_key(monkeypatch, name, variable):
    monkeypatch.delenv(variable, raising=False)

    with pytest.raises(RuntimeError, match=variable):
        create_adapter(name)


def test_unknown_provider_is_refused():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_adapter("definitely-not-a-provider")
