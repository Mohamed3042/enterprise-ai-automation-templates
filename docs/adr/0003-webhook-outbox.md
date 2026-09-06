# 0003 — A transactional outbox, not a fire-and-forget POST

Status: accepted · 2026-09-05

## Context

Outbound webhooks tell a calling system what a human decided. If the delivery is attempted
inside the request that records the decision, three things go wrong: a slow receiver slows the
approval, a crash between the commit and the POST loses the event forever, and a rollback after
a successful POST announces a decision the database does not have.

## Decision

- The engine writes an `outbox_events` row **in the same transaction** as the state change,
  through one `event_sink` seam. Nothing is sent from that request.
- A separate pass fans events out to matching subscriptions (`webhook_deliveries`) and attempts
  each one: five attempts, 5s, 30s, 2m, 10m with jitter, a receipt per attempt (status code,
  latency, response snippet), then `dead_letter`.
- The worker runs as a FastAPI lifespan task when `ATMPL_WEBHOOK_WORKER=1`, or as
  `atmpl webhooks deliver` from a scheduler. Both call the same function, so the scheduled path
  is not a second implementation that can drift.
- A dead-lettered delivery is retried by a human from `/webhooks` or
  `POST /api/v1/webhooks/deliveries/{id}/retry`.
- Delivery is **at-least-once**. `X-ATMPL-Delivery` is stable across retries so receivers can
  deduplicate, and `docs/webhooks.md` states this rather than implying exactly-once.

## Alternatives considered

- *A queue (Redis, RabbitMQ, Celery).* The right answer at scale, and a second running service
  here. The outbox table is the part that makes correctness possible; the transport is
  swappable behind `deliver_pending`.
- *Delivering inside the request.* Rejected for the three reasons above.
- *A `notify`/`LISTEN` trigger in PostgreSQL.* Ties the design to one database, which ADR 0004
  deliberately avoids.

## Consequences

- Ordering is per event, not global: two events can be delivered out of order if the first
  receiver is slow. Receivers that care should order on the body's `created_at`.
- A subscription's signing secret must be reproducible by the sender, so it is stored
  recoverably. It is shown once, masked afterwards, never logged — and named as a limitation in
  `SECURITY.md`, with `ATMPL_SECRET_WEBHOOK_INBOUND_<SOURCE>` overriding the stored value for
  inbound sources.
- The same signature scheme is used in both directions, so `docs/webhooks.md` teaches one
  recipe and the project's own test receiver verifies it.
