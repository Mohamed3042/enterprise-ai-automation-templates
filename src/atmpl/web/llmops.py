"""What the LLMOps page renders, and where each number comes from.

Two sources, deliberately kept apart and labelled on the page:

* the **model calls** come from this process's in-memory ledger — real calls, this process
  only, gone on restart;
* the **per-template approvals and blocks** come from the database — real records, and they
  survive a restart.

Nothing is generated to fill a chart. An empty deployment renders empty tables.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from atmpl.engine.database import Run, Stage, Workflow
from atmpl.runtime import AppContext

RECENT_CALLS = 25
RECENT_TRACES = 3


def template_rollup(context: AppContext) -> list[dict[str, Any]]:
    """Approvals against blocks, per source template, straight from the run records."""
    with context.engine.database.session() as session:
        rows = session.execute(
            select(
                Workflow.template_id,
                Stage.status,
                Stage.decision,
                func.count(Stage.id),
            )
            .join(Run, Run.id == Stage.run_id)
            .join(Workflow, Workflow.id == Run.workflow_id)
            .group_by(Workflow.template_id, Stage.status, Stage.decision)
        ).all()
        run_counts = dict(
            session.execute(
                select(Workflow.template_id, func.count(func.distinct(Run.id)))
                .join(Run, Run.workflow_id == Workflow.id)
                .group_by(Workflow.template_id)
            ).all()
        )

    grouped: dict[str, dict[str, int]] = {}
    for template, status, decision, count in rows:
        entry = grouped.setdefault(
            template,
            {"approved": 0, "rejected": 0, "blocked": 0, "pending": 0},
        )
        if status == "completed" and decision:
            entry["approved"] += count
        elif status == "rejected":
            entry["rejected"] += count
        elif status in {"blocked", "escalated", "parked"}:
            entry["blocked"] += count
        elif status in {"pending", "draft_ready"}:
            entry["pending"] += count
    return [
        {"template": template, "runs": run_counts.get(template, 0), **counts}
        for template, counts in sorted(grouped.items())
    ]


def llmops_view(context: AppContext) -> dict[str, Any]:
    observability = context.observability
    return {
        "totals": observability.calls.totals(),
        "summaries": observability.calls.summaries(),
        "recent": observability.calls.records(limit=RECENT_CALLS),
        "traces": observability.spans.traces(limit=RECENT_TRACES),
        "exporter": observability.exporter,
        "templates": template_rollup(context),
        "providers": context.router.status(),
        "prices": context.router.prices,
        "buffer_size": observability.calls.capacity,
    }
