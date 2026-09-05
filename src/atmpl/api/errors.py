"""One error envelope for the whole API: ``{"error": {code, message, details}}``."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from atmpl.catalog import CatalogError
from atmpl.guardrails.decisions import GuardrailRejection
from atmpl.intake.resolver import DiscoveryError

API_PREFIX = "/api/v1"


class ErrorBody(BaseModel):
    code: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="One human sentence describing what went wrong.")
    details: Any | None = Field(default=None, description="Optional structured context.")


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Raised anywhere in the API; rendered as the shared envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


def envelope(
    status_code: int,
    code: str,
    message: str,
    *,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status_code, headers=headers)


def _wants_envelope(request: Request) -> bool:
    """HTML dashboard routes keep their own rendering; only the API is enveloped."""
    return request.url.path.startswith(API_PREFIX)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return envelope(
            exc.status_code,
            exc.code,
            exc.message,
            details=exc.details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # `ctx` can carry a live exception object, so encode before it reaches json.dumps.
        details = jsonable_encoder(exc.errors(), exclude={"input", "url"})
        if not _wants_envelope(request):
            return JSONResponse({"detail": details}, status_code=422)
        return envelope(
            422,
            "validation_error",
            "The request body or query string did not validate.",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if not _wants_envelope(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        return envelope(
            exc.status_code,
            codes.get(exc.status_code, "http_error"),
            str(exc.detail),
            headers=dict(getattr(exc, "headers", None) or {}),
        )

    @app.exception_handler(GuardrailRejection)
    async def _guardrail(request: Request, exc: GuardrailRejection) -> JSONResponse:
        return envelope(
            403,
            "guardrail_blocked",
            str(exc),
            details={"guardrail": exc.guardrail},
        )

    @app.exception_handler(DiscoveryError)
    async def _discovery(request: Request, exc: DiscoveryError) -> JSONResponse:
        return envelope(
            422,
            "discovery_incomplete",
            "Discovery answers are incomplete or invalid.",
            details={"follow_ups": str(exc).splitlines()},
        )

    @app.exception_handler(CatalogError)
    async def _catalog(request: Request, exc: CatalogError) -> JSONResponse:
        return envelope(404, "template_not_found", str(exc))

    @app.exception_handler(KeyError)
    async def _key_error(request: Request, exc: KeyError) -> JSONResponse:
        return envelope(404, "not_found", str(exc.args[0] if exc.args else "Not found."))
