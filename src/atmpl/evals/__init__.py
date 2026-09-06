"""The scored eval suite: cases as data, probes as code, a report with a threshold gate."""

from atmpl.evals.cases import GATED_CATEGORIES, Case, Expectation, load_cases
from atmpl.evals.probes import REGISTRY, Observation, ProbeContext
from atmpl.evals.runner import (
    CaseOutcome,
    NoCasesFound,
    Report,
    build_router,
    latest_report,
    run_suite,
    write_report,
)

__all__ = [
    "GATED_CATEGORIES",
    "REGISTRY",
    "Case",
    "CaseOutcome",
    "Expectation",
    "NoCasesFound",
    "Observation",
    "ProbeContext",
    "Report",
    "build_router",
    "latest_report",
    "load_cases",
    "run_suite",
    "write_report",
]
