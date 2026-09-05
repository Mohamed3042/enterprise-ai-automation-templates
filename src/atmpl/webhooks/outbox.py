"""Transactional outbox and the delivery worker.

The event row is written inside the same transaction as the state change it describes, so
an event can never claim something the database did not commit. Fan-out and delivery are a
separate, restartable step: attempts back off, every attempt leaves a receipt, and the
fifth failure moves the delivery to ``dead_letter`` where a human can retry it.
"""

from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from atmpl.engine.database import OutboxEvent, WebhookDelivery, WebhookSubscription, utcnow
from atmpl.runtime import AppContext
from atmpl.webhooks.signing import SIGNATURE_HEADER, sign

#: Every event type this system publishes. Documented in docs/webhooks.md.
EVENT_TYPES = (
    "run.started",
    "run.stage.decided",
    "run.completed",
    "guardrail.blocked",
    "webhook.received",
)

BACKOFF_SECONDS = (5, 30, 120, 600)
RESPONSE_SNIPPET_CHARS = 500


def record_event(
    session: Session,
    event_type: str,
    payload: dict[str, Any],
    *,
    organization_id: str | None = None,
    run_id: str | None = None,
    stage_id: str | None = None,
) -> OutboxEvent:
    """Append one event to the outbox. Call inside the caller's open transaction."""
    event = OutboxEvent(
        event_type=event_type,
        organization_id=organization_id,
        run_id=run_id,
        stage_id=stage_id,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


def _matches(subscription: WebhookSubscription, event_type: str) -> bool:
    return not subscription.event_types or event_type in subscription.event_types


def fan_out(context: AppContext) -> int:
    """Create one pending delivery per (new event, matching active subscription)."""
    created = 0
    with context.engine.database.session() as session:
        events = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.fanned_out_at.is_(None))
            .order_by(OutboxEvent.created_at)
        ).all()
        if not events:
            return 0
        subscriptions = session.scalars(
            select(WebhookSubscription).where(WebhookSubscription.active.is_(True))
        ).all()
        for event in events:
            for subscription in subscriptions:
                if not _matches(subscription, event.event_type):
                    continue
                session.add(
                    WebhookDelivery(
                        subscription_id=subscription.id,
                        event_id=event.id,
                        event_type=event.event_type,
                        payload=_body(event),
                        status="pending",
                        next_attempt_at=utcnow(),
                    )
                )
                created += 1
            event.fanned_out_at = utcnow()
        session.commit()
    return created


def _body(event: OutboxEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.event_type,
        "created_at": event.created_at.isoformat(),
        "organization_id": event.organization_id,
        "run_id": event.run_id,
        "stage_id": event.stage_id,
        "data": event.payload,
    }


def canonical_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _next_delay(attempts: int) -> int:
    base = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
    return base + random.randint(0, max(1, base // 4))


def attempt_delivery(
    context: AppContext,
    delivery_id: str,
    *,
    client: httpx.Client | None = None,
) -> str:
    """Make one HTTP attempt and record its receipt. Returns the resulting status."""
    with context.engine.database.session() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        if delivery is None or delivery.status in {"delivered", "dead_letter"}:
            return delivery.status if delivery else "missing"
        subscription = session.get(WebhookSubscription, delivery.subscription_id)
        if subscription is None:
            delivery.status = "dead_letter"
            session.commit()
            return "dead_letter"
        url, secret = subscription.url, subscription.secret
        payload = dict(delivery.payload)

    body = canonical_body(payload)
    headers = {
        "content-type": "application/json",
        SIGNATURE_HEADER: sign(secret, body),
        "X-ATMPL-Event": str(payload.get("type", "")),
        "X-ATMPL-Delivery": delivery_id,
    }
    started = time.perf_counter()
    status_code: int | None = None
    snippet = ""
    owned = client is None
    client = client or httpx.Client(timeout=context.settings.webhook_timeout_seconds)
    try:
        response = client.post(url, content=body, headers=headers)
        status_code = response.status_code
        snippet = response.text[:RESPONSE_SNIPPET_CHARS]
    except httpx.HTTPError as exc:
        snippet = f"{type(exc).__name__}: {exc}"[:RESPONSE_SNIPPET_CHARS]
    finally:
        if owned:
            client.close()
    latency_ms = (time.perf_counter() - started) * 1000

    with context.engine.database.session() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        delivery.attempts += 1
        delivery.last_status_code = status_code
        delivery.last_latency_ms = round(latency_ms, 3)
        delivery.last_response = snippet
        delivery.receipts = [
            *(delivery.receipts or []),
            {
                "attempt": delivery.attempts,
                "at": datetime.now(UTC).isoformat(),
                "status_code": status_code,
                "latency_ms": round(latency_ms, 3),
                "response": snippet[:200],
            },
        ]
        if status_code is not None and 200 <= status_code < 300:
            delivery.status = "delivered"
        elif delivery.attempts >= context.settings.webhook_max_attempts:
            delivery.status = "dead_letter"
        else:
            delivery.status = "pending"
            delivery.next_attempt_at = utcnow() + timedelta(
                seconds=_next_delay(delivery.attempts)
            )
        delivery.updated_at = utcnow()
        result = delivery.status
        session.commit()
    return result


def due_delivery_ids(context: AppContext, *, ignore_backoff: bool = False) -> list[str]:
    with context.engine.database.session() as session:
        statement = select(WebhookDelivery.id).where(WebhookDelivery.status == "pending")
        if not ignore_backoff:
            statement = statement.where(WebhookDelivery.next_attempt_at <= utcnow())
        return list(session.scalars(statement.order_by(WebhookDelivery.created_at)))


def deliver_pending(context: AppContext, *, ignore_backoff: bool = False) -> dict[str, int]:
    """One worker pass: fan out new events, then attempt every due delivery."""
    fan_out(context)
    outcomes: dict[str, int] = {}
    with httpx.Client(timeout=context.settings.webhook_timeout_seconds) as client:
        for delivery_id in due_delivery_ids(context, ignore_backoff=ignore_backoff):
            status = attempt_delivery(context, delivery_id, client=client)
            outcomes[status] = outcomes.get(status, 0) + 1
    return outcomes


def retry_delivery(context: AppContext, delivery_id: str) -> bool:
    """Move a dead-lettered delivery back into the queue for one more round."""
    with context.engine.database.session() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        if delivery is None or delivery.status == "delivered":
            return False
        delivery.status = "pending"
        delivery.attempts = 0
        delivery.next_attempt_at = utcnow()
        delivery.updated_at = utcnow()
        session.commit()
    return True
