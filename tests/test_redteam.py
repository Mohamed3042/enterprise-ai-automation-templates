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



def test_the_red_team_writes_nothing_into_the_working_directory(tmp_path, monkeypatch):
    """A container that honours its own securityContext has a read-only root filesystem.

    R5 used to create `.redteam_tmp` beside the application, which is an OSError there — and
    seeding runs the red team, so the pod died before serving. The kind smoke job in CI found
    it; nothing on a developer machine would have.
    """
    from atmpl.redteam.contracts import run_all

    monkeypatch.chdir(tmp_path)

    outcomes = run_all(demos=("retail",))

    assert all(outcome.status == "BLOCKED" for outcome in outcomes)
    assert list(tmp_path.iterdir()) == [], "the red team must not write into the working directory"
