"""Observability: traces, metrics, structured logs, and the model-call ledger.

One :class:`Observability` object is built per application and hung on ``AppContext``, so
handlers, the engine and the provider router all read the same instruments instead of
reaching for module-level globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atmpl.settings import Settings
from atmpl.telemetry.context import CallContext, call_context, current_call_context
from atmpl.telemetry.ledger import CallLedger, LlmCallRecord, ProviderSummary
from atmpl.telemetry.logs import JsonFormatter, configure_json_logging
from atmpl.telemetry.metrics import Metrics, build_metrics
from atmpl.telemetry.tracing import (
    SPAN_GUARDRAIL,
    SPAN_LLM_CALL,
    SPAN_RUN,
    SPAN_STAGE,
    MemorySpanStore,
    Telemetry,
    active_telemetry,
    current_span_id,
    current_trace_id,
    guardrail_span,
    install_global,
    instrument_app,
    llm_call_span,
    mark_allowed,
    mark_blocked,
    reset_global,
    run_span,
    stage_span,
)

__all__ = [
    "SPAN_GUARDRAIL",
    "SPAN_LLM_CALL",
    "SPAN_RUN",
    "SPAN_STAGE",
    "CallContext",
    "CallLedger",
    "JsonFormatter",
    "LlmCallRecord",
    "MemorySpanStore",
    "Metrics",
    "Observability",
    "ProviderSummary",
    "Telemetry",
    "active_telemetry",
    "build_metrics",
    "build_observability",
    "call_context",
    "configure_json_logging",
    "current_call_context",
    "current_span_id",
    "current_trace_id",
    "guardrail_span",
    "install_global",
    "instrument_app",
    "llm_call_span",
    "mark_allowed",
    "mark_blocked",
    "reset_global",
    "run_span",
    "stage_span",
]


@dataclass
class Observability:
    """The instruments of one deployment, built once and shared."""

    telemetry: Telemetry
    metrics: Metrics
    calls: CallLedger
    enabled_metrics: bool = field(default=True)

    @property
    def spans(self) -> MemorySpanStore:
        return self.telemetry.memory

    @property
    def exporter(self) -> str:
        return self.telemetry.exporter_name


def build_observability(settings: Settings, *, install: bool = True) -> Observability:
    telemetry = Telemetry(settings)
    if install:
        install_global(telemetry)
    if settings.json_logs:
        configure_json_logging()
    return Observability(
        telemetry=telemetry,
        metrics=build_metrics(),
        calls=CallLedger(capacity=settings.telemetry_buffer_size),
        enabled_metrics=settings.metrics_enabled,
    )
