"""Risk-tier transitions. HIGH intentionally has no no-human overload or flag."""

from __future__ import annotations

from dataclasses import dataclass

from atmpl.guardrails.constants import (
    G1_RISK_TIERS,
    G2_HIGH_REQUIRES_SIGNED_HUMAN,
    G4_ATTRIBUTED_APPROVAL,
)
from atmpl.models import DecisionValue, SignedHumanDecision, StageStatus


class GuardrailRejection(ValueError):
    def __init__(self, guardrail: str, message: str) -> None:
        super().__init__(message)
        self.guardrail = guardrail


@dataclass(frozen=True)
class DecisionOutcome:
    status: StageStatus
    decision: SignedHumanDecision | None
    guardrail: str


def validate_authority(decision: SignedHumanDecision, allowed_roles: list[str]) -> None:
    if decision.role not in allowed_roles:
        raise GuardrailRejection(
            G4_ATTRIBUTED_APPROVAL,
            f"Role '{decision.role}' is outside the authority matrix: {', '.join(allowed_roles)}",
        )


def complete_low() -> DecisionOutcome:
    """G1's sole automatic completion path; it cannot receive a risk tier."""
    return DecisionOutcome(StageStatus.COMPLETED, None, G1_RISK_TIERS)


def approve_medium(
    decision: SignedHumanDecision,
    allowed_roles: list[str],
) -> DecisionOutcome:
    validate_authority(decision, allowed_roles)
    status = (
        StageStatus.COMPLETED
        if decision.decision == DecisionValue.APPROVE
        else StageStatus.REJECTED
    )
    return DecisionOutcome(status, decision, G1_RISK_TIERS)


def decide_high(
    decision: SignedHumanDecision,
    allowed_roles: list[str],
) -> DecisionOutcome:
    """The only HIGH transition: a validated SignedHumanDecision is mandatory."""
    if not isinstance(decision, SignedHumanDecision):
        raise GuardrailRejection(
            G2_HIGH_REQUIRES_SIGNED_HUMAN,
            "HIGH-risk transitions require a validated signed human decision object.",
        )
    validate_authority(decision, allowed_roles)
    status = (
        StageStatus.COMPLETED
        if decision.decision == DecisionValue.APPROVE
        else StageStatus.REJECTED
    )
    return DecisionOutcome(status, decision, G2_HIGH_REQUIRES_SIGNED_HUMAN)

