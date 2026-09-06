"""The in-process record of what this deployment's models actually did.

Every number the LLMOps page shows about a model call comes from here, and every entry
here was written by a real call in this process. Nothing is seeded, sampled or projected:
a fresh process shows "no model calls recorded yet" and means it.

Deliberately a bounded ring buffer, not a table. A model call is operational telemetry, not
a governed record — the governed record is the hash-chained audit ledger, which survives a
restart. ``/llmops`` says which of the two it is reading. [INFERRED]
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from statistics import median
from typing import Any


@dataclass(frozen=True)
class LlmCallRecord:
    """One completed attempt against one provider."""

    started_at: datetime
    provider: str
    model: str
    latency_ms: float
    outcome: str  # ok | error
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_estimate_usd: float | None = None
    cost_basis: str | None = None
    error_kind: str | None = None
    attempt: int = 1
    fallback_from: str | None = None
    run_id: str | None = None
    stage_key: str | None = None
    template_key: str | None = None
    purpose: str = "unknown"
    trace_id: str | None = None
    #: Set later by the engine when this call's output was blocked by a guardrail.
    guardrail_decision: str = "not_evaluated"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        return data


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Small samples are the norm here, so no interpolation."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


@dataclass
class ProviderSummary:
    provider: str
    model: str
    calls: int = 0
    errors: int = 0
    guardrail_trips: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate_usd: float = 0.0
    cost_measured_calls: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def p50_ms(self) -> float:
        return _percentile(self.latencies_ms, 0.50)

    @property
    def p95_ms(self) -> float:
        return _percentile(self.latencies_ms, 0.95)

    @property
    def error_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0

    @property
    def median_ms(self) -> float:
        return median(self.latencies_ms) if self.latencies_ms else 0.0


class CallLedger:
    """Thread-safe bounded log of model calls, plus the aggregates the page renders."""

    def __init__(self, capacity: int = 2_000) -> None:
        self._records: deque[LlmCallRecord] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._started_at = datetime.now(UTC)

    @property
    def started_at(self) -> datetime:
        return self._started_at

    @property
    def capacity(self) -> int:
        return self._records.maxlen or 0

    def record(self, record: LlmCallRecord) -> LlmCallRecord:
        with self._lock:
            self._records.append(record)
        return record

    def mark_guardrail(self, *, run_id: str, stage_key: str, decision: str) -> bool:
        """Attach a guardrail verdict to the newest call made for that stage.

        The engine learns the verdict after the call returns, so the record is replaced
        rather than written twice — one call, one row.
        """
        with self._lock:
            for index in range(len(self._records) - 1, -1, -1):
                candidate = self._records[index]
                if (
                    candidate.run_id == run_id
                    and candidate.stage_key == stage_key
                    and candidate.outcome == "ok"
                    and candidate.guardrail_decision == "not_evaluated"
                ):
                    self._records[index] = replace(candidate, guardrail_decision=decision)
                    return True
        return False

    def records(self, limit: int | None = None) -> list[LlmCallRecord]:
        with self._lock:
            items = list(self._records)
        items.reverse()
        return items[:limit] if limit else items

    def summaries(self) -> list[ProviderSummary]:
        grouped: dict[tuple[str, str], ProviderSummary] = {}
        for record in self.records():
            key = (record.provider, record.model)
            summary = grouped.setdefault(key, ProviderSummary(record.provider, record.model))
            summary.calls += 1
            summary.latencies_ms.append(record.latency_ms)
            if record.outcome != "ok":
                summary.errors += 1
            if record.guardrail_decision == "blocked":
                summary.guardrail_trips += 1
            summary.tokens_in += record.tokens_in or 0
            summary.tokens_out += record.tokens_out or 0
            if record.cost_estimate_usd is not None:
                summary.cost_estimate_usd += record.cost_estimate_usd
                summary.cost_measured_calls += 1
        return sorted(grouped.values(), key=lambda item: (-item.calls, item.provider))

    def totals(self) -> dict[str, Any]:
        records = self.records()
        priced = [r.cost_estimate_usd for r in records if r.cost_estimate_usd is not None]
        return {
            "calls": len(records),
            "errors": sum(1 for r in records if r.outcome != "ok"),
            "guardrail_trips": sum(1 for r in records if r.guardrail_decision == "blocked"),
            "tokens_in": sum(r.tokens_in or 0 for r in records),
            "tokens_out": sum(r.tokens_out or 0 for r in records),
            "cost_estimate_usd": sum(priced),
            "priced_calls": len(priced),
            "unpriced_calls": len(records) - len(priced),
            "providers": len({(r.provider, r.model) for r in records}),
        }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
