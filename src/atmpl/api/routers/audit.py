"""Read the hash-chained ledger, and recompute it on demand."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from atmpl.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, decode_cursor, encode_cursor
from atmpl.api.schemas import AuditEntryOut, AuditVerificationOut
from atmpl.audit import verify_audit
from atmpl.engine.database import AuditEvent
from atmpl.security.dependencies import context_of, require_scope
from atmpl.security.principals import Scope

router = APIRouter(tags=["audit"], dependencies=[Depends(require_scope(Scope.AUDIT_READ))])


@router.get(
    "/audit/entries",
    response_model=Page[AuditEntryOut],
    summary="Page through audit entries",
    description="Newest first. Each entry carries the previous hash and its own record hash.",
)
async def list_entries(
    request: Request,
    event_type: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(default=None, description="Opaque cursor from `next_cursor`."),
) -> Page[AuditEntryOut]:
    context = context_of(request)
    marker = decode_cursor(cursor)
    with context.engine.database.session() as session:
        statement = select(AuditEvent).order_by(AuditEvent.sequence.desc())
        if event_type:
            statement = statement.where(AuditEvent.event_type == event_type)
        if run_id:
            statement = statement.where(AuditEvent.run_id == run_id)
        if "sequence" in marker:
            statement = statement.where(AuditEvent.sequence < int(marker["sequence"]))
        rows = session.scalars(statement.limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[AuditEntryOut](
        items=[
            AuditEntryOut(
                event_id=row.event_id,
                sequence=row.sequence,
                event_type=row.event_type,
                actor=row.actor,
                organization_id=row.organization_id,
                run_id=row.run_id,
                stage_id=row.stage_id,
                payload=row.payload,
                prev_hash=row.prev_hash,
                record_hash=row.record_hash,
                timestamp=row.timestamp,
            )
            for row in rows
        ],
        has_more=has_more,
        next_cursor=encode_cursor({"sequence": rows[-1].sequence}) if has_more and rows else None,
    )


@router.post(
    "/audit/verify",
    response_model=AuditVerificationOut,
    summary="Recompute the whole hash chain",
    description="Reads the JSONL ledger from disk and re-derives every SHA-256 link.",
)
async def verify(request: Request) -> AuditVerificationOut:
    context = context_of(request)
    result = verify_audit(context.engine.audit.path)
    return AuditVerificationOut(
        valid=result.valid,
        count=result.count,
        last_hash=result.last_hash,
        error=result.error,
    )
