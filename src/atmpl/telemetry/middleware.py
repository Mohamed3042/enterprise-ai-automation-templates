"""HTTP-level instruments: one Prometheus sample per request, on the route template.

Labelling by the *template* (``/runs/{run_id}``) rather than the path keeps cardinality
bounded — a counter labelled with every run id is a memory leak with a graph on top.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from atmpl.telemetry import Observability

UNMATCHED = "unmatched"


def route_template(request: Request) -> str:
    """The matched route's path, or a single bucket for everything unmatched."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if not path:
        return UNMATCHED
    root = request.scope.get("root_path") or ""
    return f"{root}{path}"


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, observability: Observability) -> None:
        super().__init__(app)
        self.observability = observability

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        template = route_template(request)
        metrics = self.observability.metrics
        metrics.http_requests.labels(request.method, template, str(response.status_code)).inc()
        metrics.http_duration.labels(request.method, template).observe(elapsed)
        return response
