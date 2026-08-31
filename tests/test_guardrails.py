import inspect

import pytest

from atmpl.guardrails.decisions import (
    GuardrailRejection,
    approve_medium,
    complete_low,
    decide_high,
)
from atmpl.guardrails.policy import DeterministicPolicyEngine
from atmpl.models import SignedHumanDecision, StageStatus


def decision(role: str = "authorized") -> SignedHumanDecision:
    return SignedHumanDecision(
        actor="Synthetic Reviewer",
        role=role,
        decision="approve",
        reason="Synthetic reviewed evidence is sufficient.",
        signature="sig_synthetic_test",
    )


def test_high_transition_has_only_signed_decision_and_roles_parameters():
    assert list(inspect.signature(decide_high).parameters) == ["decision", "allowed_roles"]


def test_high_transition_rejects_non_decision_object():
    with pytest.raises(GuardrailRejection, match="signed human decision"):
        decide_high({"decision": "approve"}, ["authorized"])  # type: ignore[arg-type]


def test_low_is_the_only_automatic_completion_path():
    outcome = complete_low()

    assert outcome.status == StageStatus.COMPLETED
    assert outcome.decision is None


def test_medium_and_high_require_authorized_role():
    for transition in (approve_medium, decide_high):
        with pytest.raises(GuardrailRejection, match="outside the authority matrix"):
            transition(decision("synthetic_intern"), ["authorized"])


def test_preflight_redacts_sensitive_values_before_adapter():
    result = DeterministicPolicyEngine().preflight(
        {"national_id": "FAKE-123", "safe": "keep"}
    )

    assert result.allowed
    assert result.sanitized == {"national_id": "[REDACTED]", "safe": "keep"}


def test_postflight_enforces_schema_forbidden_content_and_bounds():
    result = DeterministicPolicyEngine().postflight(
        {"score": 101, "summary": "Another customer customer_999@example.invalid"},
        required_fields={"score", "summary", "recommendation"},
        numeric_bounds={"score": (0, 100)},
    )

    assert not result.allowed
    assert {item.code for item in result.violations} == {
        "FORBIDDEN_CONTENT",
        "OUTPUT_SCHEMA",
        "NUMERIC_BOUNDS",
    }

