"""Structured logs that carry the trace they belong to.

One JSON object per line, with ``trace_id`` and ``span_id`` taken from the active
OpenTelemetry context, so a log line found in ``kubectl logs`` opens the right trace in
Jaeger without a correlation id of its own.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from atmpl.telemetry.tracing import current_span_id, current_trace_id

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a log record as one JSON line, keeping any extra fields it carries."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id = current_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
            payload["span_id"] = current_span_id()
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    """Replace the root handlers with one JSON stream handler (idempotent)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
