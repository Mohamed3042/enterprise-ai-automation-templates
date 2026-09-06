"""Contract tests for the provider adapters and the router around them.

The hosted adapters are tested against recorded bodies (`tests/cassettes/`), because two of
the three have no key on this machine. What is tested is the whole of what this repository
actually owns: the request it builds, the response it parses, and the error class it
assigns. What is *not* tested here is that the vendor still answers that way — that is what
the `live` Gemini test is for.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from atmpl.evals.cassettes import parse_recorded
from atmpl.providers import (
    AnthropicProvider,
    GeminiProvider,
    Message,
    MockProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderRouter,
    load_prices,
)
from atmpl.providers.base import Usage, status_to_kind
from atmpl.providers.gemini import to_api_schema
from atmpl.providers.router import AllProvidersFailed, CircuitBreaker, build_provider
from atmpl.settings import Settings
from atmpl.telemetry import build_observability

CASSETTES = Path(__file__).resolve().parent / "cassettes"


def cassette(provider: str, name: str) -> dict:
    return json.loads((CASSETTES / provider / f"{name}.json").read_text(encoding="utf-8"))


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- request shape


def test_gemini_request_carries_system_instruction_schema_and_tools():
    provider = GeminiProvider(api_key="synthetic", model="gemini-3.6-flash")

    body = provider.build_request(
        [Message("system", "You draft, you never decide."), Message("user", "Draft it.")],
        schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        max_tokens=256,
    )

    assert body["system_instruction"]["parts"][0]["text"] == "You draft, you never decide."
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Draft it."}]}]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseSchema"]["properties"]["summary"]["type"] == "string"
    assert body["generationConfig"]["maxOutputTokens"] == 256


def test_gemini_schema_reduction_drops_what_the_api_rejects():
    """`responseSchema` takes an OpenAPI subset; `$ref`, `$defs` and `additionalProperties`
    are 400s, and an untyped node has to be given a type or the model fills nothing in."""
    reduced = to_api_schema(
        {
            "$defs": {"Row": {"type": "object", "properties": {"key": {"type": "string"}}}},
            "type": "object",
            "additionalProperties": False,
            "title": "Draft",
            "properties": {
                "rows": {"type": "array", "items": {"$ref": "#/$defs/Row"}},
                "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["rows"],
        }
    )

    assert "additionalProperties" not in reduced
    assert "title" not in reduced
    assert reduced["properties"]["rows"]["items"]["properties"]["key"]["type"] == "string"
    assert reduced["properties"]["note"] == {"type": "string", "nullable": True}


def test_anthropic_forces_the_structured_output_tool():
    provider = AnthropicProvider(api_key="synthetic")

    body = provider.build_request(
        [Message("system", "Rules."), Message("user", "Draft it.")],
        schema={"type": "object", "properties": {"summary": {"type": "string"}}},
    )

    assert body["system"] == "Rules."
    assert body["messages"] == [{"role": "user", "content": "Draft it."}]
    assert body["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
    assert body["tools"][0]["input_schema"]["properties"]["summary"]["type"] == "string"


def test_openai_compatible_request_uses_json_schema_response_format():
    provider = OpenAICompatibleProvider(api_key="synthetic", base_url="http://localhost:11434/v1")

    body = provider.build_request(
        [Message("user", "Draft it.")],
        schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        temperature=0.0,
    )

    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "atmpl_structured_output"
    assert body["temperature"] == 0.0
    assert provider.base_url == "http://localhost:11434/v1"


# --------------------------------------------------------------------------- response parsing


def test_gemini_counts_reasoning_tokens_as_output():
    """A thinking model bills its thoughts. Counting only the answer under-reports 30x."""
    result = parse_recorded("gemini", cassette("gemini", "complete_json")["body"])

    assert result.json["recommendation"] == "REVIEW"
    assert result.usage.input_tokens == 1043
    assert result.usage.output_tokens == 61 + 1844
    assert result.finish_reason == "STOP"


def test_gemini_parses_a_function_call():
    result = parse_recorded("gemini", cassette("gemini", "tool_call")["body"])

    assert result.tool_calls[0].name == "list_required_fields"
    assert result.tool_calls[0].arguments == {"template": "retail_refund"}
    assert result.json is None


def test_gemini_without_candidates_is_a_malformed_response_not_a_crash():
    with pytest.raises(ProviderError) as caught:
        parse_recorded("gemini", cassette("gemini", "no_candidates")["body"])

    assert caught.value.kind == "malformed_response"
    assert not caught.value.retryable


def test_anthropic_reads_the_forced_tool_input_as_the_answer():
    result = parse_recorded("anthropic", cassette("anthropic", "complete_json")["body"])

    assert result.json["recommendation"] == "REVIEW"
    assert result.tool_calls == ()
    assert result.usage == Usage(input_tokens=988, output_tokens=74)
    assert result.model == "claude-sonnet-5"


def test_anthropic_text_only_answer_has_no_structured_output():
    result = parse_recorded("anthropic", cassette("anthropic", "text_only")["body"])

    assert result.json is None
    assert "human approver" in result.text


def test_openai_parses_content_and_usage():
    result = parse_recorded("openai", cassette("openai", "complete_json")["body"])

    assert result.json["summary"].startswith("Synthetic request")
    assert result.usage == Usage(input_tokens=871, output_tokens=58)


def test_openai_tool_call_arguments_are_decoded():
    result = parse_recorded("openai", cassette("openai", "tool_call")["body"])

    assert result.tool_calls[0].arguments == {"template": "retail_refund"}


def test_openai_malformed_tool_arguments_map_to_a_provider_error():
    with pytest.raises(ProviderError) as caught:
        parse_recorded("openai", cassette("openai", "bad_tool_arguments")["body"])

    assert caught.value.kind == "malformed_response"


# --------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("provider", "name", "kind", "retryable"),
    [
        ("gemini", "error_429", "rate_limited", True),
        ("gemini", "error_503", "server_error", True),
        ("gemini", "error_401", "auth", False),
        ("anthropic", "error_429", "rate_limited", True),
        ("anthropic", "error_529", "server_error", True),
        ("anthropic", "error_401", "auth", False),
        ("openai", "error_429", "rate_limited", True),
        ("openai", "error_500", "server_error", True),
        ("openai", "error_401", "auth", False),
    ],
)
def test_recorded_error_status_maps_to_the_right_class(provider, name, kind, retryable):
    recorded = cassette(provider, name)

    mapped = status_to_kind(recorded["status"])
    error = ProviderError(mapped, "recorded", provider=provider, status_code=recorded["status"])

    assert mapped == kind
    assert error.retryable is retryable


def test_a_timeout_is_retryable_and_names_the_provider(monkeypatch):
    def explode(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", explode)

    with pytest.raises(ProviderError) as caught:
        GeminiProvider(api_key="synthetic").complete([Message("user", "hello")])

    assert caught.value.kind == "timeout"
    assert caught.value.retryable
    assert "gemini" in str(caught.value)


# --------------------------------------------------------------------------- the router


class FlakyProvider:
    """Fails `failures` times with `kind`, then answers."""

    name = "flaky"
    model = "flaky-1"

    def __init__(self, failures: int, kind: str = "server_error") -> None:
        self.remaining = failures
        self.kind = kind
        self.calls = 0

    def configured(self) -> bool:
        return True

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise ProviderError(self.kind, "synthetic failure", provider=self.name)
        return MockProvider().complete(messages, **kwargs)


def build_router(providers, **overrides) -> ProviderRouter:
    config = settings(**overrides)
    return ProviderRouter(
        config,
        observability=build_observability(config, install=False),
        providers=providers,
        sleep=lambda _seconds: None,
    )


def test_a_retryable_failure_is_retried_and_recorded_as_two_calls():
    flaky = FlakyProvider(failures=1)
    router = build_router([flaky])

    result = router.complete([Message("user", "draft")])

    assert flaky.calls == 2
    assert result.attempt == 2
    records = router.observability.calls.records()
    assert [record.outcome for record in records] == ["ok", "error"]
    assert records[1].error_kind == "server_error"


def test_a_non_retryable_failure_is_not_retried():
    flaky = FlakyProvider(failures=1, kind="bad_request")
    router = build_router([flaky, MockProvider()])

    result = router.complete([Message("user", "draft")])

    assert flaky.calls == 1
    assert result.provider == "mock"
    assert result.fallback_from == "flaky"


def test_the_chain_falls_through_to_the_next_provider():
    dead = FlakyProvider(failures=99)
    router = build_router([dead, MockProvider()], provider_max_attempts=2)

    result = router.complete([Message("user", "draft")])

    assert result.provider == "mock"
    assert result.fallback_from == "flaky"
    assert dead.calls == 2


def test_all_providers_failing_names_every_reason():
    router = build_router([FlakyProvider(failures=99)], provider_max_attempts=1)

    with pytest.raises(AllProvidersFailed) as caught:
        router.complete([Message("user", "draft")])

    assert "flaky" in caught.value.failures
    assert "synthetic failure" in caught.value.failures["flaky"]


def test_an_unconfigured_provider_is_skipped_not_called():
    router = build_router([GeminiProvider(api_key=""), MockProvider()])

    result = router.complete([Message("user", "draft")])

    assert result.provider == "mock"


def test_the_circuit_breaker_opens_then_recovers_after_the_cooldown():
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=30)

    breaker.record_failure("gemini", now=0.0)
    assert not breaker.is_open("gemini", now=0.0)
    breaker.record_failure("gemini", now=1.0)

    assert breaker.is_open("gemini", now=1.0)
    assert not breaker.is_open("gemini", now=40.0)


def test_an_open_breaker_skips_the_provider_entirely():
    dead = FlakyProvider(failures=99)
    router = build_router([dead, MockProvider()], provider_max_attempts=1)
    router.breaker.record_failure("flaky")
    router.breaker.record_failure("flaky")
    router.breaker.record_failure("flaky")

    result = router.complete([Message("user", "draft")])

    assert result.provider == "mock"
    assert dead.calls == 0, "an open breaker must not reach the provider at all"


def test_the_settings_choose_the_chain_never_a_caller():
    config = settings(provider_fallbacks=["gemini", "mock"]).model_copy(
        update={"adapter": "anthropic"}
    )

    assert config.provider_chain == ["anthropic", "gemini", "mock"]
    assert [type(build_provider(name, config)).__name__ for name in config.provider_chain] == [
        "AnthropicProvider",
        "GeminiProvider",
        "MockProvider",
    ]


# --------------------------------------------------------------------------- cost


def test_cost_is_estimated_from_the_committed_table():
    prices = load_prices()

    estimate = prices.estimate("gemini", "gemini-3.6-flash", Usage(1_000_000, 1_000_000))

    assert estimate == pytest.approx(0.75 + 3.75)
    assert prices.basis("gemini", "gemini-3.6-flash").startswith("list price")


def test_an_unpriced_model_is_not_priced_at_zero():
    """RL 003: a cost we did not compute is not a cost of nothing."""
    prices = load_prices()

    assert prices.estimate("gemini", "some-unlisted-model", Usage(1000, 1000)) is None
    assert prices.basis("gemini", "some-unlisted-model") == "not priced"


def test_unreported_usage_produces_no_estimate():
    prices = load_prices()

    assert prices.estimate("gemini", "gemini-3.6-flash", Usage(None, None)) is None


def test_the_price_table_states_when_it_was_read():
    prices = load_prices()

    assert prices.as_of
    assert prices.source
