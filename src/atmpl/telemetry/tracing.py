"""OpenTelemetry wiring: one tracer provider, an exporter chosen by settings, and the
four span shapes this system emits.

The in-memory processor is always installed, whatever the exporter is, because the LLMOps
page reads the span tree of the most recent operations from this process. ``none`` means
"export nowhere", not "trace nothing" — that keeps the keyless default honest: no network
call is made and no collector is required, and the page still shows real spans.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from atmpl import __version__
from atmpl.settings import Settings

SPAN_RUN = "atmpl.run"
SPAN_STAGE = "atmpl.stage"
SPAN_GUARDRAIL = "atmpl.guardrail"
SPAN_LLM_CALL = "atmpl.llm.call"

TRACER_NAME = "atmpl"


class MemorySpanStore(SpanProcessor):
    """Keeps the most recent finished spans so the dashboard can show a real trace."""

    def __init__(self, capacity: int = 2_000) -> None:
        self._capacity = capacity
        self._spans: list[ReadableSpan] = []
        self._lock = threading.Lock()

    def on_start(self, span, parent_context=None) -> None:  # pragma: no cover - no-op
        return None

    def on_end(self, span: ReadableSpan) -> None:
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > self._capacity:
                del self._spans[: len(self._spans) - self._capacity]

    def shutdown(self) -> None:  # pragma: no cover - process teardown
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # pragma: no cover
        return True

    def finished_spans(self) -> list[ReadableSpan]:
        with self._lock:
            return list(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def traces(self, limit: int = 5) -> list[dict[str, Any]]:
        """Group the buffered spans into traces, newest first, for the LLMOps page."""
        grouped: dict[int, list[ReadableSpan]] = {}
        for span in self.finished_spans():
            context = span.get_span_context()
            grouped.setdefault(context.trace_id, []).append(span)
        traces = []
        for trace_id, spans in grouped.items():
            # Spans arrive innermost-first (a parent ends last), and on Windows several
            # spans in one operation share a start_time to the tick. Sorting by start time
            # alone therefore leaves a stable sort holding the *end* order, which renders
            # the tree upside down. Depth breaks the tie, and it does not depend on order.
            depths = {
                span.get_span_context().span_id: _depth(span, spans) for span in spans
            }
            ordered = sorted(
                spans,
                key=lambda item: (item.start_time or 0, depths[item.get_span_context().span_id]),
            )
            root = next(
                (span for span in ordered if depths[span.get_span_context().span_id] == 0),
                ordered[0],
            )
            traces.append(
                {
                    "trace_id": format(trace_id, "032x"),
                    "root": root.name,
                    "started_at": root.start_time,
                    "span_count": len(ordered),
                    "duration_ms": _duration_ms(root),
                    "spans": [
                        {
                            "name": span.name,
                            "duration_ms": _duration_ms(span),
                            "status": span.status.status_code.name,
                            "attributes": dict(span.attributes or {}),
                            "depth": depths[span.get_span_context().span_id],
                        }
                        for span in ordered
                    ],
                }
            )
        traces.sort(key=lambda item: item["started_at"] or 0, reverse=True)
        return traces[:limit]


def _duration_ms(span: ReadableSpan) -> float:
    if span.end_time is None or span.start_time is None:
        return 0.0
    return (span.end_time - span.start_time) / 1_000_000


def _depth(span: ReadableSpan, siblings: Sequence[ReadableSpan]) -> int:
    """How deep this span sits under the trace root, by walking parent span ids."""
    by_id = {item.get_span_context().span_id: item for item in siblings}
    depth = 0
    current = span
    while current.parent is not None and current.parent.span_id in by_id:
        current = by_id[current.parent.span_id]
        depth += 1
        if depth > 16:  # defensive: a cycle cannot happen, but never hang a page render
            break
    return depth


class Telemetry:
    """Everything the app holds onto for tracing: provider, tracer, in-memory spans."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": __version__,
                "deployment.environment": "demo" if settings.demo_mode else "configured",
            }
        )
        self.provider = TracerProvider(resource=self.resource)
        self.memory = MemorySpanStore(capacity=settings.telemetry_buffer_size)
        self.provider.add_span_processor(self.memory)
        self.exporter_name = settings.trace_exporter
        if settings.trace_exporter == "console":
            self.provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        elif settings.trace_exporter == "otlp":
            self.provider.add_span_processor(BatchSpanProcessor(_otlp_exporter(settings)))
        self.tracer = self.provider.get_tracer(TRACER_NAME, __version__)

    def shutdown(self) -> None:
        self.provider.shutdown()


def _otlp_exporter(settings: Settings):
    """Imported lazily: the OTLP exporter pulls in protobuf, which `none` never needs."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=settings.otlp_endpoint)


#: The provider this process's own spans go to. OpenTelemetry refuses to replace its global
#: once set (and logs a warning when you try), so the SDK global is installed once and this
#: module keeps its own pointer — which a test, or a second app in one process, may replace.
_ACTIVE: Telemetry | None = None


def install_global(telemetry: Telemetry) -> None:
    """Make this the active provider, and the SDK global if nothing else claimed it."""
    global _ACTIVE
    _ACTIVE = telemetry
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(telemetry.provider)


def active_telemetry() -> Telemetry | None:
    return _ACTIVE


def reset_global() -> None:
    """Forget the active provider (tests). The SDK global is never un-set; it cannot be."""
    global _ACTIVE
    _ACTIVE = None


def instrument_app(app, telemetry: Telemetry, *, db_engine=None) -> None:
    """Attach FastAPI, SQLAlchemy and httpx instrumentation to this provider."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=telemetry.provider,
        excluded_urls="health,ready,metrics",
    )
    # httpx and SQLAlchemy patch library globals, so instrumenting twice in one process is
    # a warning, not a second set of spans. Ask before patching.
    httpx_instrumentor = HTTPXClientInstrumentor()
    if not httpx_instrumentor.is_instrumented_by_opentelemetry:
        httpx_instrumentor.instrument(tracer_provider=telemetry.provider)
    if db_engine is not None:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(
            engine=db_engine,
            tracer_provider=telemetry.provider,
        )


def get_tracer():
    """This process's active tracer, falling back to whatever the SDK global is."""
    if _ACTIVE is not None:
        return _ACTIVE.tracer
    return trace.get_tracer(TRACER_NAME, __version__)


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")


def current_span_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.span_id, "016x")


def _set(span: Span, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)


@contextmanager
def run_span(*, run_id: str, operation: str, **attributes: Any) -> Iterator[Span]:
    with get_tracer().start_as_current_span(SPAN_RUN, kind=SpanKind.INTERNAL) as span:
        _set(span, {"atmpl.run_id": run_id, "atmpl.operation": operation, **attributes})
        yield span


@contextmanager
def stage_span(*, stage_key: str, **attributes: Any) -> Iterator[Span]:
    with get_tracer().start_as_current_span(SPAN_STAGE, kind=SpanKind.INTERNAL) as span:
        _set(span, {"atmpl.stage_key": stage_key, **attributes})
        yield span


@contextmanager
def guardrail_span(*, phase: str, **attributes: Any) -> Iterator[Span]:
    with get_tracer().start_as_current_span(SPAN_GUARDRAIL, kind=SpanKind.INTERNAL) as span:
        _set(span, {"atmpl.guardrail.phase": phase, **attributes})
        yield span


@contextmanager
def llm_call_span(*, provider: str, model: str, **attributes: Any) -> Iterator[Span]:
    with get_tracer().start_as_current_span(SPAN_LLM_CALL, kind=SpanKind.CLIENT) as span:
        _set(span, {"atmpl.provider": provider, "atmpl.model": model, **attributes})
        yield span


def mark_blocked(span: Span, guardrail: str, detail: str) -> None:
    """A guardrail block is an expected outcome, not an exception — but it is not OK."""
    span.set_attribute("atmpl.guardrail.decision", "blocked")
    span.set_attribute("atmpl.guardrail.code", guardrail)
    span.set_status(Status(StatusCode.ERROR, detail[:200]))


def mark_allowed(span: Span) -> None:
    span.set_attribute("atmpl.guardrail.decision", "allowed")
