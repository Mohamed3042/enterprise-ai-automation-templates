"""Server-rendered approval dashboard; every mutation calls the shared engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from atmpl.audit import verify_audit
from atmpl.engine.database import AuditEvent, Organization, RedTeamResult, Run, Stage
from atmpl.engine.service import AutomationEngine
from atmpl.guardrails.constants import G2_HIGH_REQUIRES_SIGNED_HUMAN, INVARIANTS
from atmpl.guardrails.decisions import GuardrailRejection
from atmpl.models import SignedHumanDecision

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


def create_app(engine: AutomationEngine) -> FastAPI:
    app = FastAPI(
        title="Enterprise AI Automation Templates",
        description="Synthetic demonstration of governed human-authority workflows.",
    )
    app.state.automation_engine = engine
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

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
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
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
                return templates.TemplateResponse(
                    request=request,
                    name="not_found.html",
                    context={"message": f"Run not found: {run_id}"},
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
        return templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context={
                "run": run,
                "ai_recommendation": ai_recommendation,
                "terminal_decision": terminal_decision,
            },
        )

    @app.get("/approvals", response_class=HTMLResponse)
    async def approvals(request: Request) -> HTMLResponse:
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
        return templates.TemplateResponse(
            request=request,
            name="approvals.html",
            context={"pending": pending},
        )

    @app.post("/api/runs/{run_id}/stages/{stage_key}/decision")
    async def submit_decision(request: Request, run_id: str, stage_key: str):
        json_request = _is_json_request(request)
        try:
            raw: dict[str, Any]
            if json_request:
                parsed = await request.json()
                raw = parsed if isinstance(parsed, dict) else {}
            else:
                raw = dict(await request.form())
            decision = SignedHumanDecision.model_validate(raw)
        except (ValidationError, json.JSONDecodeError) as exc:
            organization_id, stage_id = _decision_context(engine, run_id, stage_key)
            engine.audit.append(
                "approval_payload_rejected",
                actor="anonymous",
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
            return templates.TemplateResponse(
                request=request,
                name="decision_result.html",
                context={"ok": False, "detail": "Signed human decision is incomplete."},
                status_code=422,
            )

        try:
            result = engine.submit_human_decision(run_id, stage_key, decision)
        except GuardrailRejection as exc:
            if json_request:
                return JSONResponse(
                    {"status": "BLOCKED", "guardrail": exc.guardrail, "detail": str(exc)},
                    status_code=403,
                )
            return templates.TemplateResponse(
                request=request,
                name="decision_result.html",
                context={"ok": False, "detail": str(exc)},
                status_code=403,
            )
        if json_request:
            return JSONResponse(result.model_dump(mode="json"))
        return templates.TemplateResponse(
            request=request,
            name="decision_result.html",
            context={"ok": True, "detail": result.detail, "result": result},
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
        return templates.TemplateResponse(
            request=request,
            name="audit.html",
            context={
                "events": events,
                "event_types": event_types,
                "selected_event_type": event_type,
                "verification": verify_audit(engine.audit.path),
            },
        )

    @app.post("/audit/verify", response_class=HTMLResponse)
    async def audit_verify(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="audit_verification.html",
            context={"verification": verify_audit(engine.audit.path)},
        )

    @app.get("/redteam", response_class=HTMLResponse)
    async def redteam_results(request: Request) -> HTMLResponse:
        with engine.database.session() as session:
            results = session.scalars(
                select(RedTeamResult).order_by(RedTeamResult.case_id, RedTeamResult.demo)
            ).all()
        return templates.TemplateResponse(
            request=request,
            name="redteam.html",
            context={"results": results},
        )

    return app
