# Webhooks

Two directions, one signature scheme.

- **Inbound** — an external event (a helpdesk ticket, a case intake) starts a *governed* run.
  It does not start a workflow of the caller's choosing: the mapping lives in the organization
  profile, and the event is recorded as the first stage's evidence.
- **Outbound** — when a human decides, or a guardrail blocks, ATMPL tells the systems that
  subscribed. Deliveries are signed, retried with backoff, receipted per attempt, and moved to
  a dead-letter state a person can retry from `/webhooks`.

## The signature

```
X-ATMPL-Signature: t=<unix seconds>,v1=<hex hmac-sha256>
```

The MAC is computed over the byte string `<t>.<raw body>` with the shared secret. The timestamp
is **inside** the MAC, so it cannot be moved to dodge the replay window without invalidating the
signature. Verify against the **raw bytes** you received, never a re-serialized object.

Deliveries also carry `X-ATMPL-Event` (the event type) and `X-ATMPL-Delivery` (the delivery id,
stable across retries — use it for your own idempotency).

### A receiver, in 15 lines

```python
import hashlib, hmac, time
from flask import Flask, request

SECRET = "whsec_..."          # shown once when the subscription was created
TOLERANCE = 300               # seconds

app = Flask(__name__)

@app.post("/hook")
def hook():
    parts = dict(p.split("=", 1) for p in request.headers["X-ATMPL-Signature"].split(","))
    if abs(time.time() - int(parts["t"])) > TOLERANCE:
        return "stale", 401
    expected = hmac.new(
        SECRET.encode(), f'{parts["t"]}.'.encode() + request.get_data(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, parts["v1"]):
        return "bad signature", 401
    return "", 200            # any 2xx marks the delivery delivered
```

The project's own test suite runs exactly this check inside its receiver fixture, so the recipe
above is verified rather than described (`tests/test_webhooks.py`).

## Outbound: subscribe, then read the receipts

```bash
curl -X POST http://127.0.0.1:8000/api/v1/webhooks/subscriptions \
  -H "Authorization: Bearer $ATMPL_KEY" -H "content-type: application/json" \
  -d '{"url":"https://example.invalid/hook","event_types":["run.stage.decided"]}'
```

The response contains `secret` **once**; every later read returns `null`. Scope required:
`webhooks:manage`.

| Event | When |
|---|---|
| `run.started` | A governed run is created (API, CLI, or an inbound webhook) |
| `run.stage.decided` | A signed human decision moved a MEDIUM or HIGH stage |
| `run.completed` | Every stage of a run reached a terminal state, or the run was rejected |
| `guardrail.blocked` | Deterministic policy blocked a stage before or after the model |
| `webhook.received` | An inbound event was accepted and mapped |

An empty `event_types` list means *every* event.

Delivery body:

```json
{
  "id": "evt_…", "type": "run.stage.decided", "created_at": "…",
  "organization_id": "org_gulf_horizon", "run_id": "run_bank_pending", "stage_id": "stage_…",
  "data": { "stage_key": "terminal_decision", "decision": "reject", "risk_tier": "HIGH",
            "actor": "Mariam Synthetic", "role": "credit_officer_tier_2",
            "principal": "human:synthetic.admin", "reason": "…", "guardrail": "G2" }
}
```

### Delivery guarantees, stated honestly

- The event row is written **in the same transaction** as the state change it describes
  (a transactional outbox), so no delivery can claim something the database did not commit.
- Attempts: 5 by default (`ATMPL_WEBHOOK_MAX_ATTEMPTS`), backing off 5s → 30s → 2m → 10m with
  jitter. Each attempt appends a receipt: status code, latency, and a 200-character response
  snippet. Measured example: [`docs/proof/webhook_delivery_receipts.txt`](proof/webhook_delivery_receipts.txt).
- After the last failure the delivery becomes `dead_letter` and stays there until a human
  retries it — from the `/webhooks` page, or `POST /api/v1/webhooks/deliveries/{id}/retry`.
- **At-least-once**, not exactly-once. A 2xx that your side loses will be retried. Deduplicate
  on `X-ATMPL-Delivery` or the body's `id`.
- The worker is in-process when `ATMPL_WEBHOOK_WORKER=1`; otherwise run `atmpl webhooks deliver`
  from a scheduler. Both call the same function.

## Inbound: an event becomes a governed run

```bash
BODY='{"ticket":{"id":"SYN-TCK-8801","refund_amount":125,"subject":"Returned on day 12"}}'
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -r | cut -d' ' -f1)
curl -X POST http://127.0.0.1:8000/api/v1/webhooks/helpdesk \
  -H "content-type: application/json" \
  -H "X-ATMPL-Signature: t=$TS,v1=$SIG" \
  -H "X-Idempotency-Key: SYN-TCK-8801" \
  -d "$BODY"
```

`python -m atmpl demo up` prints the per-source secrets on startup and writes them to
`var/demo-webhook-secrets.json`; `atmpl webhooks sources` prints them again.

Rules:

- **Signature required.** No valid signature, no run — the 401 path is measured in the tests
  by counting runs before and after.
- **Five-minute tolerance** (`ATMPL_WEBHOOK_TOLERANCE_SECONDS`).
- **Idempotent.** `X-Idempotency-Key` (or, absent one, the SHA-256 of the body) is unique per
  source. A replay returns the first result with `idempotent_replay: true` and starts nothing.
- **The caller does not choose the workflow.** It comes from the mapping (below).
- **G3 still applies.** The projected fields go through the same deterministic redaction and
  injection checks the engine runs before a model call, so a `customer_email` lands in the
  evidence as `[REDACTED]`.

### Declaring a source

The mapping lives under `webhooks.inbound` in the organization's profile:

```json
{
  "webhooks": {
    "inbound": {
      "helpdesk": {
        "workflow_id": "wf_retail_eu",
        "title": "Helpdesk ticket {ticket_id} - refund review",
        "region": "EU",
        "field_map": {
          "ticket_id": "ticket.id",
          "amount": "ticket.refund_amount",
          "reason": "ticket.subject",
          "customer_email": "ticket.requester.email"
        },
        "secret": "whsec_…"
      }
    }
  }
}
```

`field_map` maps a target field to a dotted path in the incoming payload; anything not mapped
never reaches the workflow. The two demo sources (`helpdesk` for retail, `case_intake` for the
ministry) are seeded by `atmpl demo up` — see `DEMO_INBOUND_SOURCES` in `src/atmpl/demos.py`.

A secret in the provider takes precedence over the stored one, which is how a real deployment
keeps it out of the database:

```bash
export ATMPL_SECRET_WEBHOOK_INBOUND_HELPDESK=whsec_from_your_vault
```

## Seeing it end to end

`docker compose --profile receiver up` starts a small receiver beside the API, so a decision on
the dashboard produces a visible delivery with receipts on `/webhooks`.
