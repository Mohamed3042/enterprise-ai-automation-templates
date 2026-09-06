"""Run the cases, score them, write the report.

Scoring is deterministic: an observation matches an expectation or it does not. No model
judges another model here — `docs/evals.md` says why, and what would have to change before
a judge could be added.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atmpl.evals.cases import CASES_ROOT, Case, load_cases
from atmpl.evals.probes import REGISTRY, Observation, ProbeContext
from atmpl.providers.router import ProviderRouter
from atmpl.settings import Settings
from atmpl.telemetry import Observability, build_observability

REPORT_ROOT = Path("var") / "evals"


class NoCasesFound(RuntimeError):
    """The suite loaded nothing.

    An empty run is a broken harness, not a passing system -- and it is easy to cause: the
    container image did not ship `evals/` at first, so the suite ran zero cases and reported
    a pass rate for them. Refusing is the only honest answer.
    """

    def __init__(self, root: Path) -> None:
        super().__init__(
            f"No eval cases were found under {root}. Nothing was scored, so there is no "
            "pass rate to report. Check that the `evals/cases/` directory shipped with this "
            "build."
        )
        self.root = root


@dataclass
class CaseOutcome:
    id: str
    title: str
    category: str
    probe: str
    gated: bool
    status: str  # pass | fail | error | skipped
    expected: str
    observed: str
    codes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    detail: str = ""
    latency_ms: float = 0.0
    provider: str = ""
    model: str = ""

    @property
    def counted(self) -> bool:
        """Skipped cases are reported but never scored — a skip is not a pass."""
        return self.status in {"pass", "fail", "error"}


def evaluate(case: Case, observation: Observation) -> list[str]:
    """Every way this observation misses the expectation, in plain sentences."""
    reasons: list[str] = []
    expect = case.expect
    if observation.decision != expect.decision:
        reasons.append(f"decision was '{observation.decision}', expected '{expect.decision}'")
    missing = [code for code in expect.codes if code not in observation.codes]
    if missing:
        observed = ", ".join(observation.codes) or "-"
        reasons.append(f"missing code(s) {', '.join(missing)} (observed: {observed})")
    if expect.guardrail and observation.guardrail != expect.guardrail:
        reasons.append(
            f"guardrail was '{observation.guardrail}', expected '{expect.guardrail}'"
        )
    for forbidden in expect.must_not_contain:
        if forbidden.lower() in observation.rendered.lower():
            reasons.append(f"output contained forbidden text {forbidden!r}")
    return reasons


def run_case(case: Case, context: ProbeContext, *, live: bool) -> CaseOutcome:
    outcome = CaseOutcome(
        id=case.id,
        title=case.title,
        category=case.category,
        probe=case.probe,
        gated=case.gated,
        status="skipped",
        expected=case.expect.decision,
        observed="-",
        provider=context.router.primary.name,
        model=context.router.primary.model,
    )
    if case.requires_live and not live:
        outcome.detail = "needs a hosted provider; run with --provider gemini"
        return outcome
    started = time.perf_counter()
    try:
        observation = REGISTRY[case.probe](context, case.args)
    except Exception as exc:  # a probe that explodes is a finding, not a crash
        outcome.status = "error"
        outcome.observed = "error"
        outcome.detail = f"{type(exc).__name__}: {exc}"
        outcome.latency_ms = (time.perf_counter() - started) * 1000
        return outcome
    outcome.latency_ms = (time.perf_counter() - started) * 1000
    outcome.observed = observation.decision
    outcome.codes = list(observation.codes)
    outcome.detail = observation.detail
    outcome.reasons = evaluate(case, observation)
    outcome.status = "pass" if not outcome.reasons else "fail"
    return outcome


@dataclass
class Report:
    started_at: str
    finished_at: str
    provider: str
    model: str
    live: bool
    outcomes: list[CaseOutcome]

    # ----------------------------------------------------------------- scores

    @property
    def scored(self) -> list[CaseOutcome]:
        return [outcome for outcome in self.outcomes if outcome.counted]

    @property
    def passed(self) -> int:
        return sum(1 for outcome in self.scored if outcome.status == "pass")

    @property
    def skipped(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == "skipped")

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.scored) if self.scored else 0.0

    @property
    def gated_outcomes(self) -> list[CaseOutcome]:
        return [outcome for outcome in self.scored if outcome.gated]

    @property
    def gated_pass_rate(self) -> float:
        gated = self.gated_outcomes
        return sum(1 for o in gated if o.status == "pass") / len(gated) if gated else 0.0

    def by_category(self) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for outcome in self.outcomes:
            blank = {"total": 0, "pass": 0, "fail": 0, "error": 0, "skipped": 0}
            entry = grouped.setdefault(outcome.category, {**blank, "gated": outcome.gated})
            entry["total"] += 1
            entry[outcome.status] += 1
        return dict(sorted(grouped.items()))

    def latency_ms(self) -> dict[str, float]:
        values = sorted(o.latency_ms for o in self.scored)
        if not values:
            return {"p50": 0.0, "p95": 0.0, "max": 0.0}
        return {
            "p50": values[len(values) // 2],
            "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
            "max": values[-1],
        }

    # ----------------------------------------------------------------- output

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "provider": self.provider,
            "model": self.model,
            "live": self.live,
            "totals": {
                "cases": len(self.outcomes),
                "scored": len(self.scored),
                "passed": self.passed,
                "failed": sum(1 for o in self.scored if o.status == "fail"),
                "errored": sum(1 for o in self.scored if o.status == "error"),
                "skipped": self.skipped,
                "pass_rate": round(self.pass_rate, 4),
                "gated_pass_rate": round(self.gated_pass_rate, 4),
            },
            "latency_ms": {k: round(v, 3) for k, v in self.latency_ms().items()},
            "by_category": self.by_category(),
            "cases": [asdict(outcome) for outcome in self.outcomes],
        }

    def to_markdown(self) -> str:
        totals = self.as_dict()["totals"]
        lines = [
            "# ATMPL eval report",
            "",
            f"- provider: **{self.provider}** (`{self.model}`), "
            f"{'live' if self.live else 'deterministic offline'}",
            f"- run: {self.started_at} to {self.finished_at}",
            f"- pass rate: **{totals['pass_rate']:.3f}** "
            f"({totals['passed']}/{totals['scored']} scored, {totals['skipped']} skipped)",
            f"- gated pass rate: **{totals['gated_pass_rate']:.3f}** "
            f"({len(self.gated_outcomes)} gated case(s))",
            f"- case latency: p50 {self.latency_ms()['p50']:.1f} ms, "
            f"p95 {self.latency_ms()['p95']:.1f} ms",
            "",
            "| category | cases | pass | fail | error | skipped | gated |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for name, entry in self.by_category().items():
            lines.append(
                f"| {name} | {entry['total']} | {entry['pass']} | {entry['fail']} | "
                f"{entry['error']} | {entry['skipped']} | {'yes' if entry['gated'] else 'no'} |"
            )
        failures = [o for o in self.outcomes if o.status in {"fail", "error"}]
        lines += ["", f"## Failures ({len(failures)})", ""]
        if not failures:
            lines.append("None.")
        for outcome in failures:
            why = "; ".join(outcome.reasons) or outcome.detail
            lines.append(f"- **{outcome.id}** {outcome.title} — {why}")
        lines += [
            "",
            "## Every case",
            "",
            "| id | category | expected | observed | status |",
            "| --- | --- | --- | --- | --- |",
        ]
        for outcome in self.outcomes:
            lines.append(
                f"| {outcome.id} | {outcome.category} | {outcome.expected} | "
                f"{outcome.observed} | {outcome.status} |"
            )
        return "\n".join(lines) + "\n"


def build_router(
    provider: str | None,
    observability: Observability | None = None,
) -> ProviderRouter:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    if provider:
        settings = settings.model_copy(update={"adapter": provider, "provider_fallbacks": []})
    observability = observability or build_observability(settings, install=False)
    return ProviderRouter(settings, observability=observability)


def run_suite(
    *,
    provider: str | None = None,
    cases_root: Path | None = None,
    only: list[str] | None = None,
    router: ProviderRouter | None = None,
) -> Report:
    router = router or build_router(provider)
    live = router.primary.name != "mock"
    context = ProbeContext(router=router)
    root = cases_root or CASES_ROOT
    loaded = load_cases(cases_root)
    if not loaded:
        raise NoCasesFound(root)
    cases = [case for case in loaded if not only or case.id in set(only)]
    started = datetime.now(UTC).isoformat()
    outcomes = [run_case(case, context, live=live) for case in cases]
    return Report(
        started_at=started,
        finished_at=datetime.now(UTC).isoformat(),
        provider=router.primary.name,
        model=router.primary.model,
        live=live,
        outcomes=outcomes,
    )


def write_report(report: Report, root: Path | None = None) -> tuple[Path, Path]:
    """Write `<ts>.json` plus `<ts>.md`, and update `latest.json` / `latest.md`."""
    directory = root or REPORT_ROOT
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.finished_at.replace(":", "-").replace("+00:00", "Z")
    json_path = directory / f"{stamp}.json"
    md_path = directory / f"{stamp}.md"
    payload = json.dumps(report.as_dict(), indent=2, ensure_ascii=False)
    json_path.write_text(payload, encoding="utf-8")
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    (directory / "latest.json").write_text(payload, encoding="utf-8")
    (directory / "latest.md").write_text(report.to_markdown(), encoding="utf-8")
    return json_path, md_path


def latest_report(root: Path | None = None) -> dict[str, Any] | None:
    path = (root or REPORT_ROOT) / "latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
