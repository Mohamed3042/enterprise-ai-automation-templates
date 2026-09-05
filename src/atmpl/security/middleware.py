"""Request-scoped hardening: request ids, body cap, rate limit, security headers."""

from __future__ import annotations

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from atmpl.api.errors import envelope
from atmpl.runtime import AppContext

REQUEST_ID_HEADER = "X-Request-Id"
API_PREFIX = "/api/v1"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Echo the caller's ``X-Request-Id``, or mint one, so logs and clients agree."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Refuse oversized bodies before any handler or parser sees them."""

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return envelope(
                413,
                "payload_too_large",
                f"Request body exceeds the {self.max_bytes}-byte limit.",
            )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token bucket per credential (or per client host when anonymous)."""

    def __init__(self, app, context: AppContext) -> None:
        super().__init__(app)
        self.context = context

    def _key(self, request: Request) -> str:
        header = request.headers.get("authorization", "")
        if header:
            return f"cred:{hash(header) & 0xFFFFFFFF:08x}"
        cookie = request.cookies.get("atmpl_session")
        if cookie:
            return f"sess:{hash(cookie) & 0xFFFFFFFF:08x}"
        client = request.client.host if request.client else "unknown"
        return f"host:{client}"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.context.limiter.enabled or request.url.path in {"/health", "/api/v1/health"}:
            return await call_next(request)
        allowed, retry_after = self.context.limiter.take(self._key(request))
        if not allowed:
            return envelope(
                429,
                "rate_limited",
                f"Rate limit {self.context.settings.rate_limit} exceeded for this credential.",
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response headers. The CSP is written for the server-rendered dashboard."""

    def __init__(self, app, context: AppContext) -> None:
        super().__init__(app)
        self.context = context
        script_src = " ".join(["'self'", *context.settings.csp_script_src])
        self.csp = "; ".join(
            [
                "default-src 'self'",
                f"script-src {script_src}",
                "style-src 'self'",
                "img-src 'self' data:",
                "connect-src 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                "base-uri 'none'",
                "object-src 'none'",
            ]
        )
        self.docs_csp = self.csp.replace(
            "script-src 'self'",
            "script-src 'self' https://cdn.jsdelivr.net",
        ).replace("style-src 'self'", "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        is_docs = request.url.path.endswith(("/docs", "/redoc"))
        response.headers.setdefault(
            "Content-Security-Policy", self.docs_csp if is_docs else self.csp
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        if self.context.settings.hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
