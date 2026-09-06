"""Discovery over HTTP: ask the typed questions, validate answers, compile a workflow.

This is the CLI's `init` / `resolve` pair exposed as resources, so an external system can
run the same fail-closed intake the consultant runs at a whiteboard.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, Path, Request

from atmpl.api.errors import ApiError
from atmpl.api.schemas import (
    AnswersUpdate,
    AnswersValidation,
    DiscoverySessionCreate,
    DiscoverySessionOut,
    FollowUpOut,
    QuestionOut,
    ResolvedWorkflowOut,
    ResolveRequest,
)
from atmpl.catalog import canonical_name
from atmpl.engine.database import DiscoverySession, utcnow
from atmpl.intake.resolver import (
    answer_skeleton,
    compile_workflow,
    question_items,
    validate_answers,
)
from atmpl.security.dependencies import context_of, require_scope
from atmpl.security.principals import Scope

router = APIRouter(tags=["discovery"])


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "organization"


def _out(record: DiscoverySession) -> DiscoverySessionOut:
    return DiscoverySessionOut(
        id=record.id,
        template=record.template,
        organization=record.organization,
        questions=[QuestionOut(**item) for item in question_items(record.template)],
        answers=record.answers,
        resolved=record.resolved,
        workflow_id=record.workflow_id,
        created_at=record.created_at,
    )


def _load(session, session_id: str) -> DiscoverySession:
    record = session.get(DiscoverySession, session_id)
    if not record:
        raise ApiError(404, "not_found", f"Discovery session not found: {session_id}")
    return record


@router.post(
    "/discovery/sessions",
    response_model=DiscoverySessionOut,
    status_code=201,
    summary="Open a discovery session",
    description="Returns the typed questionnaire and a blank, correctly shaped answers document.",
    dependencies=[Depends(require_scope(Scope.DISCOVERY_WRITE))],
)
async def create_session(
    request: Request,
    request_data: DiscoverySessionCreate,
) -> DiscoverySessionOut:
    context = context_of(request)
    canonical = canonical_name(request_data.template)
    with context.engine.database.session() as session:
        record = DiscoverySession(
            template=canonical,
            organization=request_data.organization,
            answers=answer_skeleton(canonical, request_data.organization),
        )
        session.add(record)
        session.commit()
        return _out(record)


@router.get(
    "/discovery/sessions/{session_id}",
    response_model=DiscoverySessionOut,
    summary="Read a discovery session",
    dependencies=[Depends(require_scope(Scope.DISCOVERY_WRITE))],
)
async def get_session(
    request: Request,
    session_id: str = Path(description="Discovery session id."),
) -> DiscoverySessionOut:
    context = context_of(request)
    with context.engine.database.session() as session:
        return _out(_load(session, session_id))


@router.put(
    "/discovery/sessions/{session_id}/answers",
    response_model=AnswersValidation,
    summary="Submit answers and see what is still missing",
    description=(
        "Stores the answers and returns the outstanding follow-ups. An empty `follow_ups` "
        "list means the session can be resolved. Nothing compiles until then."
    ),
    dependencies=[Depends(require_scope(Scope.DISCOVERY_WRITE))],
)
async def put_answers(
    request: Request,
    session_id: str = Path(description="Discovery session id."),
    body: AnswersUpdate = Body(),
) -> AnswersValidation:
    context = context_of(request)
    with context.engine.database.session() as session:
        record = _load(session, session_id)
        record.answers = body.answers
        record.updated_at = utcnow()
        session.commit()
        follow_ups = validate_answers(record.template, body.answers)
        return AnswersValidation(
            id=record.id,
            valid=not follow_ups,
            follow_ups=[FollowUpOut(**item) for item in follow_ups],
        )


@router.post(
    "/discovery/sessions/{session_id}/resolve",
    response_model=ResolvedWorkflowOut,
    summary="Compile the session into a governed workflow",
    description=(
        "Fails closed with `discovery_incomplete` and a numbered follow-up list while any "
        "typed answer is missing or invalid."
    ),
    dependencies=[Depends(require_scope(Scope.DISCOVERY_WRITE))],
)
async def resolve_session(
    request: Request,
    session_id: str = Path(description="Discovery session id."),
    body: ResolveRequest = Body(default=ResolveRequest()),
) -> ResolvedWorkflowOut:
    context = context_of(request)
    with context.engine.database.session() as session:
        record = _load(session, session_id)
        template, answers, organization = record.template, record.answers, record.organization

    workflow = compile_workflow(template, answers)
    organization_id = body.organization_id or f"org_{_slug(organization)}"
    workflow_id = f"wf_{_slug(organization)}_{_slug(template)}"

    context.engine.create_organization(
        organization_id=organization_id,
        name=organization,
        sector=workflow.metadata.sector,
        profile={**workflow.org_profile, "synthetic": True},
    )
    context.engine.create_workflow(
        workflow_id=workflow_id,
        organization_id=organization_id,
        workflow=workflow,
    )

    with context.engine.database.session() as session:
        record = _load(session, session_id)
        record.resolved = True
        record.workflow_id = workflow_id
        record.updated_at = utcnow()
        session.commit()

    return ResolvedWorkflowOut(
        session_id=session_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        template=template,
        name=workflow.metadata.name,
        stage_count=len(workflow.stages),
        org_profile=workflow.org_profile,
        workflow=workflow.model_dump(mode="json"),
    )
