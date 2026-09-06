"""Liveness and readiness. `/health` never touches the database; `/ready` does."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from atmpl import __version__
from atmpl.api.schemas import HealthOut, ReadyOut
from atmpl.security.dependencies import context_of

router = APIRouter(tags=["ops"])


@router.get(
    "/health",
    response_model=HealthOut,
    summary="Liveness probe",
    description="Answers as long as the process is serving. Used by Docker HEALTHCHECK.",
)
async def health(request: Request) -> HealthOut:
    settings = context_of(request).settings
    return HealthOut(status="ok", version=__version__, adapter=settings.adapter)


@router.get(
    "/ready",
    response_model=ReadyOut,
    summary="Readiness probe",
    description="Returns 503 while the configured database cannot be reached.",
)
async def ready(request: Request, response: Response) -> ReadyOut:
    context = context_of(request)
    try:
        with context.engine.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - the probe reports any driver failure
        response.status_code = 503
        return ReadyOut(status="degraded", database="unreachable", detail=type(exc).__name__)
    return ReadyOut(status="ready", database="reachable")
