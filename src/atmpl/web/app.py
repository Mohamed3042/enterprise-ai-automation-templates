"""Server-rendered approval dashboard plus the mounted `/api/v1`.

Every mutation on either surface calls the same engine function, so the dashboard, the API
and the tests cannot drift into three different sets of rules.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from atmpl import __version__
from atmpl.api import API_PREFIX, create_api
from atmpl.api.errors import ApiError, envelope, install_error_handlers
from atmpl.audit import verify_audit
from atmpl.engine.database import (
    AuditEvent,
    Organization,
    OutboxEvent,
    RedTeamResult,
    Run,
    Stage,
    WebhookDelivery,
    WebhookSubscription,
)
from atmpl.engine.service import AutomationEngine
from atmpl.evals.runner import latest_report
from atmpl.guardrails.constants import G2_HIGH_REQUIRES_SIGNED_HUMAN, INVARIANTS
from atmpl.guardrails.decisions import GuardrailRejection
from atmpl.models import SignedHumanDecision
from atmpl.runtime import AppContext, build_context
from atmpl.security.credentials import verify_password
from atmpl.security.dependencies import SESSION_COOKIE, dashboard_principal, session_principal
from atmpl.security.middleware import (
    BodyLimitMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from atmpl.security.principals import Scope
from atmpl.security.tokens import SessionData, TokenError, read_session, sign_session
from atmpl.settings import Settings, settings_from_env
from atmpl.telemetry import build_observability, instrument_app
from atmpl.telemetry.metrics import CONTENT_TYPE
from atmpl.telemetry.middleware import MetricsMiddleware
from atmpl.web.llmops import llmops_view
from atmpl.webhooks.outbox import deliver_pending, record_event, retry_delivery

WEB_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_ROOT / "templates")
templates.env.filters["prettyjson"] = lambda value: json.dumps(
    value,
    ensure_ascii=False,
    indent=2,
)


def _is_json_request(request: Request) -> bool:
    return "application/json" in request.headers.get("content-type", "")


def _decision_context(engine: AutomationEngine, run_id: str, stage_key: str) -> tuple[str, str]:
    with engine.database.session() as session:
        stage = session.scalar(
            select(Stage)
            .options(joinedload(Stage.run))
            .where(Stage.run_id == run_id, Stage.stage_key == stage_key)
        )
        if not stage:
            return "unknown", "unknown"
        return stage.run.organization_id, stage.id


def _csrf_token(request: Request, context: AppContext) -> str:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return ""
    try:
        return read_session(cookie, key=context.session_key).csrf
    except TokenError:
        return ""


def _check_csrf(request: Request, context: AppContext, submitted: str | None) -> None:
    """Double-submit CSRF: only signed-in, form-encoded mutations are checked."""
    expected = _csrf_token(request, context)
    if not expected or _is_json_request(request):
        return
    if not submitted or not secrets.compare_digest(submitted, expected):
        raise ApiError(403, "csrf_failed", "The form CSRF token is missing or stale.")


def create_app(
    engine: AutomationEngine,
    settings: Settings | None = None,
    *,
    observability=None,
) -> FastAPI:
    settings = settings or settings_from_env()
    observability = observability or build_observability(settings)
    context = build_context(engine, settings, observability=observability)
    engine.event_sink = record_event

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if settings.webhook_worker:
            task = asyncio.create_task(_delivery_loop(context))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="Enterprise AI Automation Templates",
        version=__version__,
        description="Synthetic demonstration of governed human-authority workflows.",
        lifespan=lifespan,
    )
    app.state.automation_engine = engine
    app.state.context = context
    install_error_handlers(app)

    instrument_app(app, observability.telemetry, db_engine=engine.database.engine)

    app.add_middleware(SecurityHeadersMiddleware, context=context)
    if settings.metrics_enabled:
        app.add_middleware(MetricsMiddleware, observability=observability)
    app.add_middleware(RateLimitMiddleware, context=context)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestIdMiddleware)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-Id", "X-Idempotency-Key"],
        )

    app.mount(API_PREFIX, create_api(context))
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    @app.middleware("http")
    async def _readonly_guard(request: Request, call_next):
        """A hosted demo may be read: it may not be changed, and it never spends a key."""
        if settings.demo_readonly and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return envelope(
                403,
                "demo_readonly",
                "This is a read-only public demo. Run it locally to make decisions: "
                "https://github.com/Mohamed3042/enterprise-ai-automation-templates",
            )
        return await call_next(request)

    def render(request: Request, name: str, context_data: dict[str, Any], **kwargs: Any):
        principal = session_principal(request)
        return templates.TemplateResponse(
            request=request,
            name=name,
            context={
                "settings": settings,
                "principal": principal,
                "demo_mode": settings.demo_mode,
                "demo_readonly": settings.demo_readonly,
                "csrf_token": _csrf_token(request, context),
                "app_version": __version__,
                **context_data,
            },
            **kwargs,
        )

    # ----------------------------------------------------------------- auth

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, error: str | None = None) -> HTMLResponse:
        if settings.demo_mode:
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html", {"error": error})

    @app.post("/login")
    async def login(request: Request, username: str = Form(), password: str = Form()):
        if settings.demo_mode:
            return RedirectResponse("/", status_code=303)
        ok = username == settings.admin_user and verify_password(
            password, settings.admin_password_hash
        )
        engine.audit.append(
            "dashboard_login_succeeded" if ok else "dashboard_login_failed",
            actor=f"human:{username}",
            payload={"outcome": "accepted" if ok else "rejected"},
        )
        if not ok:
            return render(
                request,
                "login.html",
                {"error": "Those credentials were not accepted."},
                status_code=401,
            )
        expires = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
        cookie = sign_session(
            SessionData(
                user=username,
                csrf=secrets.token_urlsafe(24),
                expires_at=int(expires.timestamp()),
            ),
            key=context.session_key,
        )
        response = RedirectResponse("/approvals", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            cookie,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            samesite="lax",
            secure=settings.hsts_enabled,
            path="/",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request):
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ----------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        with engine.database.session() as session:
            organizations = session.scalars(
                select(Organization)
                .options(
                    selectinload(Organization.workflows),
                    selectinload(Organization.runs),
                )
                .order_by(Organization.name)
            ).all()
            recent_runs = session.scalars(
                select(Run)
                .options(joinedload(Run.organization))
                .order_by(Run.created_at.desc())
                .limit(8)
            ).all()
            metrics = {
                "organizations": session.scalar(select(func.count(Organization.id))) or 0,
                "workflows": sum(len(org.workflows) for org in organizations),
                "runs": session.scalar(select(func.count(Run.id))) or 0,
                "pending": session.scalar(
                    select(func.count(Stage.id)).where(Stage.status.in_(["pending", "draft_ready"]))
                )
                or 0,
            }
        return render(
            request,
            "home.html",
            {
                "organizations": organizations,
                "recent_runs": recent_runs,
                "metrics": metrics,
                "invariants": INVARIANTS,
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str) -> HTMLResponse:
        with engine.database.session() as session:
            run = session.scalar(
                select(Run)
                .options(
                    selectinload(Run.stages),
                    joinedload(Run.organization),
                    joinedload(Run.workflow),
                )
                .where(Run.id == run_id)
            )
            if not run:
                return render(
                    request,
                    "not_found.html",
                    {"message": f"Run not found: {run_id}"},
                    status_code=404,
                )
            ai_recommendation = next(
                (
                    stage.draft.get("content", {}).get("recommendation")
                    for stage in run.stages
                    if stage.stage_key == "credit_summary"
                    and stage.draft
                    and stage.draft.get("content", {}).get("recommendation")
                ),
                None,
            )
            terminal_decision = next(
                (
                    stage.decision
                    for stage in run.stages
                    if stage.stage_key == "terminal_decision" and stage.decision
                ),
                None,
            )
            event_ids = list(
                session.scalars(select(OutboxEvent.id).where(OutboxEvent.run_id == run_id))
            )
            deliveries = (
                session.scalars(
                    select(WebhookDelivery)
                    .where(WebhookDelivery.event_id.in_(event_ids))
                    .order_by(WebhookDelivery.created_at.desc())
                ).all()
                if event_ids
                else []
            )
        return render(
            request,
            "run_detail.html",
            {
                "run": run,
                "ai_recommendation": ai_recommendation,
                "terminal_decision": terminal_decision,
                "deliveries": deliveries,
            },
        )

    def _require_page_login(request: Request):
        """HTML pages send a human to the login form; the API returns 401 JSON."""
        if session_principal(request) is None:
            return RedirectResponse("/login", status_code=303)
        return None

    @app.get("/approvals", response_class=HTMLResponse)
    async def approvals(request: Request):
        if (redirect := _require_page_login(request)) is not None:
            return redirect
        with engine.database.session() as session:
            pending = session.scalars(
                select(Stage)
                .options(joinedload(Stage.run).joinedload(Run.organization))
                .where(
                    Stage.risk_tier.in_(["MEDIUM", "HIGH"]),
                    Stage.status.in_(["pending", "draft_ready"]),
                )
                .order_by(Stage.risk_tier.desc(), Stage.position)
            ).all()
        return render(request, "approvals.html", {"pending": pending})

    @app.get("/webhooks", response_class=HTMLResponse)
    async def webhooks_page(request: Request):
        if (redirect := _require_page_login(request)) is not None:
            return redirect
        with engine.database.session() as session:
            subscriptions = session.scalars(
                select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
            ).all()
            deliveries = session.scalars(
                select(WebhookDelivery)
                .options(joinedload(WebhookDelivery.subscription))
                .order_by(WebhookDelivery.created_at.desc())
                .limit(50)
            ).all()
            counts = {
                status: session.scalar(
                    select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == status)
                )
                or 0
                for status in ("pending", "delivered", "dead_letter")
            }
        return render(
            request,
            "webhooks.html",
            {"subscriptions": subscriptions, "deliveries": deliveries, "counts": counts},
        )

    @app.post("/webhooks/deliveries/{delivery_id}/retry")
    async def retry_delivery_form(request: Request, delivery_id: str, csrf_token: str = Form("")):
        principal = dashboard_principal(request)
        if not principal.has(Scope.WEBHOOKS_MANAGE):
            raise ApiError(403, "insufficient_scope", "Missing the 'webhooks:manage' scope.")
        _check_csrf(request, context, csrf_token)
        retry_delivery(context, delivery_id)
        deliver_pending(context, ignore_backoff=True)
        return RedirectResponse("/webhooks", status_code=303)

    # ------------------------------------------- legacy decision alias (dashboard + API)

    @app.post("/api/runs/{run_id}/stages/{stage_key}/decision")
    async def submit_decision(request: Request, run_id: str, stage_key: str):
        """Compatibility alias of ``POST /api/v1/runs/.../decision``.

        Kept because the HTMX dashboard and the published v0.1 examples both call it. It
        authenticates the same way the dashboard does, so a demo deployment keeps working.
        """
        json_request = _is_json_request(request)
        principal = dashboard_principal(request)
        if not principal.has(Scope.DECISIONS_WRITE):
            raise ApiError(403, "insufficient_scope", "Missing the 'decisions:write' scope.")
        try:
            raw: dict[str, Any]
            if json_request:
                parsed = await request.json()
                raw = parsed if isinstance(parsed, dict) else {}
            else:
                raw = dict(await request.form())
                _check_csrf(request, context, raw.pop("csrf_token", None))
            decision = SignedHumanDecision.model_validate(raw)
        except (ValidationError, json.JSONDecodeError) as exc:
            organization_id, stage_id = _decision_context(engine, run_id, stage_key)
            engine.audit.append(
                "approval_payload_rejected",
                actor=principal.actor,
                organization_id=organization_id,
                run_id=run_id,
                stage_id=stage_id,
                payload={
                    "guardrail": G2_HIGH_REQUIRES_SIGNED_HUMAN,
                    "reason": "Missing or invalid signed human decision object.",
                },
            )
            detail = (
                exc.errors(include_url=False)
                if isinstance(exc, ValidationError)
                else [{"msg": "Invalid JSON"}]
            )
            if json_request:
                return JSONResponse(
                    {"status": "BLOCKED", "guardrail": "G2", "detail": detail},
                    status_code=422,
                )
            return render(
                request,
                "decision_result.html",
                {"ok": False, "detail": "Signed human decision is incomplete."},
                status_code=422,
            )

        try:
            result = engine.submit_human_decision(
                run_id, stage_key, decision, principal=principal.actor
            )
        except GuardrailRejection as exc:
            if json_request:
                return JSONResponse(
                    {"status": "BLOCKED", "guardrail": exc.guardrail, "detail": str(exc)},
                    status_code=403,
                )
            return render(
                request,
                "decision_result.html",
                {"ok": False, "detail": str(exc)},
                status_code=403,
            )
        if json_request:
            return JSONResponse(result.model_dump(mode="json"))
        return render(
            request,
            "decision_result.html",
            {"ok": True, "detail": result.detail, "result": result, "principal": principal},
        )

    @app.get("/audit", response_class=HTMLResponse)
    async def audit_view(request: Request, event_type: str | None = None) -> HTMLResponse:
        with engine.database.session() as session:
            statement = select(AuditEvent).order_by(AuditEvent.sequence.desc()).limit(100)
            if event_type:
                statement = statement.where(AuditEvent.event_type == event_type)
            events = session.scalars(statement).all()
            event_types = session.scalars(
                select(AuditEvent.event_type).distinct().order_by(AuditEvent.event_type)
            ).all()
        return render(
            request,
            "audit.html",
            {
                "events": events,
                "event_types": event_types,
                "selected_event_type": event_type,
                "verification": verify_audit(engine.audit.path),
            },
        )

    @app.post("/audit/verify", response_class=HTMLResponse)
    async def audit_verify(request: Request) -> HTMLResponse:
        return render(
            request,
            "audit_verification.html",
            {"verification": verify_audit(engine.audit.path)},
        )

    @app.get("/evals", response_class=HTMLResponse)
    async def evals_page(request: Request) -> HTMLResponse:
        with engine.database.session() as session:
            results = session.scalars(
                select(RedTeamResult).order_by(RedTeamResult.case_id, RedTeamResult.demo)
            ).all()
        return render(
            request,
            "evals.html",
            {"results": results, "report": latest_report()},
        )

    @app.get("/redteam", include_in_schema=False)
    async def redteam_results(request: Request):
        """v0.2's URL. The page grew a scored report and moved to /evals."""
        return RedirectResponse("/evals", status_code=308)

    @app.get("/llmops", response_class=HTMLResponse)
    async def llmops_page(request: Request) -> HTMLResponse:
        return render(request, "llmops.html", llmops_view(context))

    @app.get("/health", include_in_schema=False)
    async def root_health() -> JSONResponse:
        """Root alias of `/api/v1/health`, so a Kubernetes probe needs no API prefix."""
        return JSONResponse(
            {"status": "ok", "version": __version__, "adapter": settings.adapter}
        )

    @app.get("/ready", include_in_schema=False)
    async def root_ready() -> JSONResponse:
        """Root alias of `/api/v1/ready`. 503 while the database cannot be reached."""
        from sqlalchemy import text

        try:
            with engine.database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - the probe reports any driver failure
            return JSONResponse(
                {"status": "degraded", "database": "unreachable", "detail": type(exc).__name__},
                status_code=503,
            )
        return JSONResponse({"status": "ready", "database": "reachable"})

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        """Prometheus exposition. Open by default; set ATMPL_METRICS_PUBLIC=0 to gate it."""
        if not settings.metrics_enabled:
            raise ApiError(404, "not_found", "Metrics are disabled (ATMPL_METRICS_ENABLED=0).")
        if not settings.metrics_public:
            principal = dashboard_principal(request)
            if not principal.has(Scope.METRICS_READ):
                raise ApiError(403, "insufficient_scope", "Missing the 'metrics:read' scope.")
        return Response(observability.metrics.render(), media_type=CONTENT_TYPE)

    return app


async def _delivery_loop(context: AppContext) -> None:
    """Background outbound-webhook worker; the same pass `atmpl webhooks deliver` runs."""
    interval = context.settings.webhook_worker_interval_seconds
    while True:
        # A worker must survive one bad delivery; every attempt is receipted in the row.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(deliver_pending, context)
        await asyncio.sleep(interval)
