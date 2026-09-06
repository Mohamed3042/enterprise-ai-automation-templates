"""The one test that actually talks to a hosted model.

Marked `live` and skipped without `GEMINI_API_KEY`, so the default suite stays keyless and
offline. It exists because every other provider claim in this repository is a claim about a
*recorded* body: this is the one that proves the request shape, the structured-output mode
and the usage accounting still match a real API today.

Run it with: `pytest -m live`
"""

from __future__ import annotations

import os

import pytest

from atmpl.providers import GeminiProvider, Message, ProviderRouter, load_prices
from atmpl.settings import Settings
from atmpl.telemetry import build_observability

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY is not set; the hosted providers are contract-tested instead",
    ),
]

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendation": {"type": "string"},
        "completeness_score": {"type": "number", "minimum": 0, "maximum": 100},
    },
    "required": ["summary", "recommendation", "completeness_score"],
}


def live_router() -> ProviderRouter:
    settings = Settings(_env_file=None).model_copy(  # type: ignore[call-arg]
        update={"adapter": "gemini"}  # 90 s is the default, for the reason in settings.py
    )
    return ProviderRouter(settings, observability=build_observability(settings, install=False))


def test_gemini_answers_in_the_declared_schema():
    router = live_router()

    result = router.complete(
        [
            Message("system", "You draft for a human reviewer. You never decide."),
            Message(
                "user",
                "Draft a triage note for a synthetic loan application of 48,000 KWD with "
                "complete documents. Return the declared JSON object only.",
            ),
        ],
        schema=DRAFT_SCHEMA,
        max_tokens=2048,
    )

    assert result.provider == "gemini"
    assert result.json is not None, f"expected a JSON object, got: {result.text!r}"
    assert set(DRAFT_SCHEMA["required"]) <= set(result.json)
    assert 0 <= float(result.json["completeness_score"]) <= 100


def test_a_live_call_is_costed_and_recorded():
    router = live_router()

    result = router.complete([Message("user", "Reply with the single word: ready")])
    record = router.observability.calls.records()[0]

    assert result.usage.input_tokens and result.usage.input_tokens > 0
    assert record.provider == "gemini"
    assert record.latency_ms > 0
    assert record.cost_estimate_usd is not None, (
        "the configured live model must be in prices.yaml, or the dashboard shows 'not priced'"
    )
    assert load_prices().knows("gemini", record.model)


def test_an_invalid_key_is_an_auth_error_not_a_retry_storm():
    provider = GeminiProvider(api_key="definitely-not-a-valid-key")

    from atmpl.providers import ProviderError

    with pytest.raises(ProviderError) as caught:
        provider.complete([Message("user", "hello")])

    assert caught.value.kind in {"auth", "bad_request"}
    assert not caught.value.retryable
