"""Webhooks in both directions: events that start governed runs, decisions that report back."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Request
from sqlalchemy import select, tuple_

from atmpl.api.errors import ApiError
from atmpl.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Page,
    decode_row_cursor,
    encode_row_cursor,
)
from atmpl.api.schemas import DeliveryOut, InboundAck, SubscriptionCreate, SubscriptionOut
from atmpl.engine.database import InboundEvent, WebhookDelivery, WebhookSubscription
from atmpl.security.dependencies import context_of, require_scope
from atmpl.security.principals import Scope
from atmpl.webhooks.inbound import (
    MappingNotConfigured,
    body_hash,
    find_replay,
    load_mapping,
    new_inbound_secret,
    project,
)
from atmpl.webhooks.outbox import deliver_pending, record_event, retry_delivery
from atmpl.webhooks.signing import SIGNATURE_HEADER, SignatureError, verify

router = APIRouter(tags=["webhooks"])
MANAGE = Depends(require_scope(Scope.WEBHOOKS_MANAGE))


def _subscription_out(row: WebhookSubscription, *, secret: str | None = None) -> SubscriptionOut:
    return SubscriptionOut(
        id=row.id,
        url=row.url,
        description=row.description,
        event_types=list(row.event_types or []),
        active=row.active,
        created_at=row.created_at,
        secret=secret,
    )


def _delivery_out(row: WebhookDelivery) -> DeliveryOut:
    return DeliveryOut(
        id=row.id,
        subscription_id=row.subscription_id,
        event_id=row.event_id,
        event_type=row.event_type,
        status=row.status,
        attempts=row.attempts,
        last_status_code=row.last_status_code,
        last_latency_ms=row.last_latency_ms,
        last_response=row.last_response,
        receipts=list(row.receipts or []),
        next_attempt_at=row.next_attempt_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# --------------------------------------------------------------------------- outbound


@router.post(
    "/webhooks/subscriptions",
    response_model=SubscriptionOut,
    status_code=201,
    summary="Subscribe to governed events",
    description=(
        "Returns the signing secret exactly once. Every delivery to this URL carries "
        "`X-ATMPL-Signature: t=<unix>,v1=<hmac-sha256>` over `<t>.<body>`."
    ),
    dependencies=[MANAGE],
)
async def create_subscription(request: Request, body: SubscriptionCreate) -> SubscriptionOut:
    context = context_of(request)
    secret = new_inbound_secret()
    with context.engine.database.session() as session:
        row = WebhookSubscription(
            url=str(body.url),
            description=body.description,
            secret=secret,
            event_types=body.event_types,
            active=True,
        )
        session.add(row)
        session.commit()
        return _subscription_out(row, secret=secret)


@router.get(
    "/webhooks/subscriptions",
    response_model=list[SubscriptionOut],
    summary="List subscriptions",
    description="Signing secrets are never returned again after creation.",
    dependencies=[MANAGE],
)
async def list_subscriptions(request: Request) -> list[SubscriptionOut]:
    context = context_of(request)
    with context.engine.database.session() as session:
        rows = session.scalars(
            select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
        ).all()
        return [_subscription_out(row) for row in rows]


@router.delete(
    "/webhooks/subscriptions/{subscription_id}",
    status_code=204,
    summary="Delete a subscription",
    dependencies=[MANAGE],
)
async def delete_subscription(
    request: Request,
    subscription_id: str = Path(description="Subscription id."),
) -> None:
    context = context_of(request)
    with context.engine.database.session() as session:
        row = session.get(WebhookSubscription, subscription_id)
        if not row:
            raise ApiError(404, "not_found", f"Subscription not found: {subscription_id}")
        session.delete(row)
        session.commit()


@router.get(
    "/webhooks/deliveries",
    response_model=Page[DeliveryOut],
    summary="Page through delivery attempts",
    description="Each row carries every attempt's receipt: status code, latency, response.",
    dependencies=[MANAGE],
)
async def list_deliveries(
    request: Request,
    status: str | None = Query(default=None, description="pending | delivered | dead_letter"),
    subscription_id: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(default=None),
) -> Page[DeliveryOut]:
    context = context_of(request)
    marker = decode_row_cursor(cursor)
    with context.engine.database.session() as session:
        statement = select(WebhookDelivery).order_by(
            WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc()
        )
        if status:
            statement = statement.where(WebhookDelivery.status == status)
        if subscription_id:
            statement = statement.where(WebhookDelivery.subscription_id == subscription_id)
        if marker:
            statement = statement.where(
                tuple_(WebhookDelivery.created_at, WebhookDelivery.id) < marker
            )
        rows = session.scalars(statement.limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[DeliveryOut](
        items=[_delivery_out(row) for row in rows],
        has_more=has_more,
        next_cursor=encode_row_cursor(rows[-1].created_at, rows[-1].id) if has_more else None,
    )


@router.post(
    "/webhooks/deliveries/{delivery_id}/retry",
    response_model=DeliveryOut,
    summary="Retry a dead-lettered delivery",
    dependencies=[MANAGE],
)
async def retry(
    request: Request,
    delivery_id: str = Path(description="Delivery id."),
) -> DeliveryOut:
    context = context_of(request)
    if not retry_delivery(context, delivery_id):
        raise ApiError(409, "not_retryable", "That delivery is already delivered or missing.")
    deliver_pending(context, ignore_backoff=True)
    with context.engine.database.session() as session:
        row = session.get(WebhookDelivery, delivery_id)
        return _delivery_out(row)


# --------------------------------------------------------------------------- inbound


@router.post(
    "/webhooks/{source}",
    response_model=InboundAck,
    summary="Start a governed run from an external event",
    description=(
        "Verifies `X-ATMPL-Signature` against this source's shared secret inside a "
        "five-minute tolerance, then maps the payload through the mapping declared in the "
        "organization profile. Replaying an `X-Idempotency-Key` returns the first result "
        "and starts no second run."
    ),
)
async def receive(
    request: Request,
    source: str = Path(description="Configured inbound source, e.g. `helpdesk`."),
    body: Any = Body(default=None),
    signature: str | None = Header(default=None, alias=SIGNATURE_HEADER),
    idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> InboundAck:
    context = context_of(request)
    raw = await request.body()
    try:
        mapping = load_mapping(context, source)
    except MappingNotConfigured as exc:
        raise ApiError(404, "unknown_source", str(exc)) from exc

    try:
        verify(
            mapping.secret,
            signature,
            raw,
            tolerance_seconds=context.settings.webhook_tolerance_seconds,
        )
    except SignatureError as exc:
        raise ApiError(401, "invalid_signature", str(exc)) from exc

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise ApiError(400, "invalid_payload", "Body must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_payload", "Body must be a JSON object.")

    key = idempotency_key or body_hash(raw)
    with context.engine.database.session() as session:
        replay = find_replay(session, source, key)
        if replay:
            return InboundAck(
                accepted=True,
                source=source,
                run_id=replay.run_id,
                idempotent_replay=True,
                detail="Replay of an already accepted event; no second run was started.",
            )

    mapped = project(mapping, payload)
    run = context.engine.start_run(
        workflow_id=mapping.workflow_id,
        title=mapping.title.format(**{k: v for k, v in mapped.items() if v is not None})
        if "{" in mapping.title
        else mapping.title,
        region=mapping.region,
        evidence={
            "source": source,
            "idempotency_key": key,
            "body_sha256": body_hash(raw),
            "mapped": mapped,
        },
    )

    with context.engine.database.session() as session:
        session.add(
            InboundEvent(
                source=source,
                idempotency_key=key,
                body_hash=body_hash(raw),
                run_id=run.id,
                response={"run_id": run.id},
            )
        )
        record_event(
            session,
            "webhook.received",
            {"source": source, "run_id": run.id, "mapped": mapped},
            organization_id=mapping.organization_id,
            run_id=run.id,
        )
        session.commit()

    context.engine.audit.append(
        "inbound_webhook_accepted",
        actor=f"webhook:{source}",
        organization_id=mapping.organization_id,
        run_id=run.id,
        payload={"source": source, "idempotency_key": key, "body_sha256": body_hash(raw)},
    )
    return InboundAck(
        accepted=True,
        source=source,
        run_id=run.id,
        detail=f"Governed run {run.id} started; the event is recorded as stage evidence.",
    )
