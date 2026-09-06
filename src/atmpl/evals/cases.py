"""What an eval case is, and where the cases live.

A case is data: a probe to run, the arguments to run it with, and what the system is
expected to decide. Adding a case is adding a row to a YAML file, not writing a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from atmpl.catalog import PROJECT_ROOT

CASES_ROOT = PROJECT_ROOT / "evals" / "cases"

#: Cases in these categories must all pass before a build is allowed through (`evals gate`).
GATED_CATEGORIES = frozenset(
    {
        "redteam",
        "prompt_injection",
        "data_protection",
        "authority",
        "output_integrity",
        "typed_output",
    }
)


@dataclass(frozen=True)
class Expectation:
    """What the system must decide. `decision` is the contract; the rest are extras."""

    decision: str
    codes: tuple[str, ...] = ()
    guardrail: str | None = None
    must_not_contain: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    category: str
    probe: str
    expect: Expectation
    args: dict[str, Any] = field(default_factory=dict)
    #: Cases that need a hosted model are skipped (not failed) on the mock provider.
    requires_live: bool = False
    rationale: str = ""

    @property
    def gated(self) -> bool:
        return self.category in GATED_CATEGORIES


def _expectation(raw: dict[str, Any]) -> Expectation:
    return Expectation(
        decision=str(raw["decision"]),
        codes=tuple(raw.get("codes") or ()),
        guardrail=raw.get("guardrail"),
        must_not_contain=tuple(raw.get("must_not_contain") or ()),
        note=raw.get("note"),
    )


def load_cases(root: Path | None = None) -> list[Case]:
    """Every case in `evals/cases/*.yaml`, ordered by id so a report is comparable."""
    directory = root or CASES_ROOT
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in document.get("cases") or []:
            cases.append(
                Case(
                    id=str(raw["id"]),
                    title=str(raw["title"]),
                    category=str(raw.get("category", document.get("category", "uncategorised"))),
                    probe=str(raw["probe"]),
                    expect=_expectation(raw["expect"]),
                    args=dict(raw.get("args") or {}),
                    requires_live=bool(raw.get("requires_live", False)),
                    rationale=str(raw.get("rationale", "")),
                )
            )
    duplicates = sorted({c.id for c in cases if [x.id for x in cases].count(c.id) > 1})
    if duplicates:
        raise ValueError(f"Duplicate eval case id(s): {', '.join(duplicates)}")
    return sorted(cases, key=lambda case: case.id)
