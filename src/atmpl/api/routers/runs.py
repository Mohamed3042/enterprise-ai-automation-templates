"""Runs and their governed stages, including the only decision endpoint that exists."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select, tuple_
from sqlalchemy.orm import joinedload, selectinload

from atmpl.api.errors import ApiError
from atmpl.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Page,
    decode_row_cursor,
    encode_row_cursor,
)
from atmpl.api.schemas import (
    DecisionRequest,
    DecisionResult,
    RunCreate,
    RunDetail,
    RunSummary,
    StageOut,
)
from atmpl.engine.database import DiscoverySession, Run, Stage, Workflow
from atmpl.models import SignedHumanDecision
from atmpl.security.dependencies import context_of, require_scope
from atmpl.security.principals import Principal, Scope

router = APIRouter(tags=["runs"])


def _stage_out(stage: Stage) -> StageOut:
    return StageOut(
        id=stage.id,
        stage_key=stage.stage_key,
        name=stage.name,
        position=stage.position,
        action_type=stage.action_type,
        risk_tier=stage.risk_tier,
        status=stage.status,
        allowed_roles=list(stage.allowed_roles or []),
        escalation_reason=stage.escalation_reason,
        draft=stage.draft,
        decision=stage.decision,
        evidence=stage.evidence,
    )


def _summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        title=run.title,
        region=run.region,
        status=run.status,
        synthetic=run.synthetic,
        organization_id=run.organization_id,
        workflow_id=run.workflow_id,
        created_at=run.created_at,
    )


@router.post(
    "/runs",
    response_model=RunDetail,
    status_code=201,
    summary="Start a governed run",
    description=(
        "Starts a compiled workflow. The model adapter is chosen by this deployment's "
        "settings and can never be selected by the caller (G7)."
    ),
    dependencies=[Depends(require_scope(Scope.RUNS_WRITE))],
)
async def create_run(request: Request, body: RunCreate) -> RunDetail:
    context = context_of(request)
    workflow_id = body.workflow_id
    with context.engine.database.session() as session:
        if body.discovery_session_id:
            discovery = session.get(DiscoverySession, body.discovery_session_id)
            if not discovery or not discovery.workflow_id:
                raise ApiError(
                    409,
                    "session_not_resolved",
                    "Resolve the discovery session before starting a run from it.",
                )
            workflow_id = discovery.workflow_id
        workflow = session.get(Workflow, workflow_id)
        if not workflow:
            raise ApiError(404, "not_found", f"Workflow not found: {workflow_id}")
        profile = workflow.spec.get("org_profile", {})
        default_region = str(profile.get("regional", {}).get("region") or "GLOBAL")

    run = context.engine.start_run(
        workflow_id=str(workflow_id),
        title=body.title,
        region=body.region or default_region,
        evidence=body.evidence,
    )
    return await get_run(request, run_id=run.id)


@router.get(
    "/runs",
    response_model=Page[RunSummary],
    summary="List runs",
    description="Newest first. Page with the opaque `cursor` returned by the previous page.",
    dependencies=[Depends(require_scope(Scope.RUNS_READ))],
)
async def list_runs(
    request: Request,
    status: str | None = Query(default=None, description="Filter by run status."),
    organization_id: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(default=None, description="Opaque cursor from `next_cursor`."),
) -> Page[RunSummary]:
    context = context_of(request)
    marker = decode_row_cursor(cursor)
    with context.engine.database.session() as session:
        statement = select(Run).order_by(Run.created_at.desc(), Run.id.desc())
        if status:
            statement = statement.where(Run.status == status)
        if organization_id:
            statement = statement.where(Run.organization_id == organization_id)
        if marker:
            statement = statement.where(tuple_(Run.created_at, Run.id) < marker)
        rows = session.scalars(statement.limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[RunSummary](
        items=[_summary(run) for run in rows],
        has_more=has_more,
        next_cursor=encode_row_cursor(rows[-1].created_at, rows[-1].id) if has_more else None,
    )


@router.get(
    "/runs/{run_id}",
    response_model=RunDetail,
    summary="Read one run with its stages",
    dependencies=[Depends(require_scope(Scope.RUNS_READ))],
)
async def get_run(request: Request, run_id: str = Path(description="Run id.")) -> RunDetail:
    context = context_of(request)
    with context.engine.database.session() as session:
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
            raise ApiError(404, "not_found", f"Run not found: {run_id}")
        return RunDetail(
            **_summary(run).model_dump(),
            organization_name=run.organization.name,
            workflow_name=run.workflow.name,
            stages=[_stage_out(stage) for stage in run.stages],
        )


@router.get(
    "/runs/{run_id}/stages",
    response_model=list[StageOut],
    summary="List the stages of a run",
    dependencies=[Depends(require_scope(Scope.RUNS_READ))],
)
async def list_stages(
    request: Request,
    run_id: str = Path(description="Run id."),
) -> list[StageOut]:
    context = context_of(request)
    with context.engine.database.session() as session:
        run = session.scalar(select(Run).options(selectinload(Run.stages)).where(Run.id == run_id))
        if not run:
            raise ApiError(404, "not_found", f"Run not found: {run_id}")
        return [_stage_out(stage) for stage in run.stages]


@router.post(
    "/runs/{run_id}/stages/{stage_key}/decision",
    response_model=DecisionResult,
    summary="Record a signed human decision",
    description=(
        "The only path that moves a MEDIUM or HIGH stage. The authenticated principal is "
        "written into the hash-chained ledger next to the signature, so the audit answers "
        "*who decided*, not only *what was decided*."
    ),
)
async def submit_decision(
    request: Request,
    body: DecisionRequest,
    run_id: str = Path(description="Run id."),
    stage_key: str = Path(description="Stage key inside that run, e.g. `terminal_decision`."),
    principal: Principal = Depends(require_scope(Scope.DECISIONS_WRITE)),
) -> DecisionResult:
    context = context_of(request)
    decision = SignedHumanDecision.model_validate(body.model_dump(mode="json"))
    result = context.engine.submit_human_decision(
        run_id,
        stage_key,
        decision,
        principal=principal.actor,
    )
    return DecisionResult(
        accepted=result.accepted,
        status=result.status.value,
        guardrail=result.guardrail,
        detail=result.detail,
        principal=principal.actor,
    )
