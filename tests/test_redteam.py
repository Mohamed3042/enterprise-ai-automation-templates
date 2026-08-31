"""Guardrail acceptance tests, deliberately committed before implementation."""

import pytest

from atmpl.redteam.contracts import CASES


DEMOS = ("retail", "ministry", "bank")
CASE_IDS = tuple(case_id for case_id, _ in CASES)


@pytest.mark.parametrize("demo", DEMOS)
@pytest.mark.parametrize(("case_id", "case"), CASES, ids=CASE_IDS)
def test_adversarial_case_is_blocked(case_id, case, demo):
    result = case(demo)

    assert result.case_id == case_id
    assert result.demo == demo
    assert result.status == "BLOCKED"
    assert result.detail

