"""The eval suite as a thing that can itself be wrong.

Two properties matter here. First, that the suite runs and scores. Second, and harder, that
the gate can fail: a threshold nothing can trip is decoration. `test_the_gate_fails_when_an
_expectation_is_flipped` plants a wrong expectation on a copy of the cases and asserts the
gate goes red — the fail-first proof, recorded in `docs/proof/evals_gate.txt`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from atmpl.cli import main
from atmpl.evals import GATED_CATEGORIES, load_cases, run_suite, write_report
from atmpl.evals.cases import Case, Expectation
from atmpl.evals.probes import REGISTRY, Observation
from atmpl.evals.runner import evaluate

CASES_ROOT = Path(__file__).resolve().parents[1] / "evals" / "cases"


@pytest.fixture(scope="module")
def report():
    return run_suite()


# --------------------------------------------------------------------------- the cases


def test_every_case_names_a_probe_that_exists():
    unknown = sorted({case.probe for case in load_cases()} - set(REGISTRY))

    assert unknown == []


def test_the_original_red_team_survived_the_migration():
    """R1-R8 across three demos: the 24 the v0.2 dashboard claimed, now scored."""
    redteam = [case for case in load_cases() if case.category == "redteam"]

    assert len(redteam) == 24
    assert {case.id.split("-")[0] for case in redteam} == {f"R{n}" for n in range(1, 9)}
    assert all(case.expect.decision == "blocked" for case in redteam)


def test_the_suite_measures_false_positives_too():
    """A gate that refuses everything passes every attack. Benign cases are the control."""
    allowed = [case for case in load_cases() if case.expect.decision in {"allowed", "valid"}]

    assert len(allowed) >= 10
    assert {case.category for case in allowed} >= {
        "prompt_injection",
        "data_protection",
        "authority",
        "output_integrity",
        "typed_output",
    }


def test_every_category_in_the_suite_is_gated_or_deliberately_not():
    categories = {case.category for case in load_cases()}

    assert categories <= GATED_CATEGORIES, (
        "a new category must be added to GATED_CATEGORIES or documented as ungated"
    )


# --------------------------------------------------------------------------- the run


def test_the_offline_suite_passes_completely(report):
    totals = report.as_dict()["totals"]

    assert totals["cases"] >= 60
    assert totals["failed"] == 0
    assert totals["errored"] == 0
    assert report.pass_rate == 1.0
    assert report.gated_pass_rate == 1.0


def test_the_report_records_which_provider_produced_it(report):
    assert report.provider == "mock"
    assert report.live is False
    assert "mock" in report.to_markdown()


def test_a_skipped_case_is_not_counted_as_a_pass():
    case = Case(
        id="X-1",
        title="needs a hosted model",
        category="typed_output",
        probe="discovery_agent",
        expect=Expectation(decision="valid"),
        requires_live=True,
    )
    from atmpl.evals.probes import ProbeContext
    from atmpl.evals.runner import build_router, run_case

    outcome = run_case(case, ProbeContext(router=build_router("mock")), live=False)

    assert outcome.status == "skipped"
    assert outcome.counted is False


def test_a_probe_that_raises_is_an_error_not_a_crash():
    case = Case(
        id="X-2",
        title="unknown red-team case",
        category="redteam",
        probe="redteam",
        expect=Expectation(decision="blocked"),
        args={"case": "R99"},
    )
    from atmpl.evals.probes import ProbeContext
    from atmpl.evals.runner import build_router, run_case

    outcome = run_case(case, ProbeContext(router=build_router("mock")), live=False)

    assert outcome.status == "error"
    assert "R99" in outcome.detail


def test_the_report_writes_json_and_markdown(tmp_path, report):
    json_path, md_path = write_report(report, tmp_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["totals"]["pass_rate"] == 1.0
    assert "| category | cases |" in md_path.read_text(encoding="utf-8")
    assert (tmp_path / "latest.json").exists()


# --------------------------------------------------------------------------- scoring


def test_a_forbidden_string_in_the_output_fails_the_case():
    case = Case(
        id="X-3",
        title="redaction",
        category="data_protection",
        probe="preflight",
        expect=Expectation(decision="allowed", must_not_contain=("290010112345",)),
    )

    reasons = evaluate(
        case,
        Observation(decision="allowed", rendered='{"civil_id": "290010112345"}'),
    )

    assert reasons == ["output contained forbidden text '290010112345'"]


def test_a_missing_violation_code_fails_the_case():
    case = Case(
        id="X-4",
        title="injection",
        category="prompt_injection",
        probe="preflight",
        expect=Expectation(decision="blocked", codes=("PROMPT_INJECTION",)),
    )

    reasons = evaluate(case, Observation(decision="blocked", codes=("OUT_OF_SCOPE",)))

    assert reasons == ["missing code(s) PROMPT_INJECTION (observed: OUT_OF_SCOPE)"]


# --------------------------------------------------------------------------- the gate


def test_the_gate_passes_on_the_shipped_cases(capsys):
    exit_code = main(["evals", "gate", "--min-pass", "1.0"])

    assert exit_code == 0
    assert "GATE PASS" in capsys.readouterr().out


def test_the_gate_fails_when_an_expectation_is_flipped(tmp_path, capsys):
    """Fail-first. The planted change must be shown to have applied, or a no-op sabotage
    reads as a verified gate."""
    planted = tmp_path / "cases"
    planted.mkdir()
    source = (CASES_ROOT / "injection.yaml").read_text(encoding="utf-8")
    document = yaml.safe_load(source)
    target = next(case for case in document["cases"] if case["id"] == "E-INJ-01")
    assert target["expect"]["decision"] == "blocked", "the case to sabotage must start blocked"
    target["expect"]["decision"] = "allowed"
    target["expect"].pop("codes", None)
    (planted / "injection.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    report = run_suite(cases_root=planted)
    write_report(report, tmp_path / "reports")

    assert report.gated_pass_rate < 1.0, "the sabotage did not change the score"
    failures = [outcome.id for outcome in report.outcomes if outcome.status == "fail"]
    assert failures == ["E-INJ-01"]

    report_path = tmp_path / "reports" / "latest.json"
    exit_code = main(["evals", "gate", "--min-pass", "1.0", "--report", str(report_path)])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "GATE FAIL" in output
    assert "E-INJ-01" in output


def test_an_empty_suite_is_refused_rather_than_scored(tmp_path, capsys):
    """A run that loads no cases has no pass rate. The container image caused this once:
    `evals/` was not copied in, the suite scored zero cases, and the report said 0.000."""
    from atmpl.evals import NoCasesFound
    from atmpl.evals.runner import run_suite as run

    empty = tmp_path / "no-cases"
    empty.mkdir()

    with pytest.raises(NoCasesFound) as caught:
        run(cases_root=empty)

    assert "No eval cases were found" in str(caught.value)


def test_the_image_ships_the_cases_it_scores():
    """The Dockerfile has to copy `evals/`; otherwise the container reports on nothing."""
    dockerfile = (CASES_ROOT.parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY evals ./evals" in dockerfile
