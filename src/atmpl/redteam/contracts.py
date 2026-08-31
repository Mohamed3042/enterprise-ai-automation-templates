"""Red-team contracts written before their guardrail implementations.

The first commit intentionally leaves every contract unimplemented so the captured
RED-before run is meaningful without ever introducing an unsafe approval branch.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RedTeamOutcome:
    case_id: str
    demo: str
    status: str
    detail: str


def _not_wired(case_id: str, demo: str) -> RedTeamOutcome:
    raise NotImplementedError(f"{case_id} guardrail is not wired for {demo}")


def r1_prompt_injection(demo: str) -> RedTeamOutcome:
    return _not_wired("R1", demo)


def r2_high_risk_api_bypass(demo: str) -> RedTeamOutcome:
    return _not_wired("R2", demo)


def r3_role_escalation(demo: str) -> RedTeamOutcome:
    return _not_wired("R3", demo)


def r4_data_exfiltration(demo: str) -> RedTeamOutcome:
    return _not_wired("R4", demo)


def r5_audit_tamper(demo: str) -> RedTeamOutcome:
    return _not_wired("R5", demo)


def r6_kill_switch(demo: str) -> RedTeamOutcome:
    return _not_wired("R6", demo)


def r7_malformed_output(demo: str) -> RedTeamOutcome:
    return _not_wired("R7", demo)


def r8_regional_bypass(demo: str) -> RedTeamOutcome:
    return _not_wired("R8", demo)


CASES: tuple[tuple[str, Callable[[str], RedTeamOutcome]], ...] = (
    ("R1", r1_prompt_injection),
    ("R2", r2_high_risk_api_bypass),
    ("R3", r3_role_escalation),
    ("R4", r4_data_exfiltration),
    ("R5", r5_audit_tamper),
    ("R6", r6_kill_switch),
    ("R7", r7_malformed_output),
    ("R8", r8_regional_bypass),
)

