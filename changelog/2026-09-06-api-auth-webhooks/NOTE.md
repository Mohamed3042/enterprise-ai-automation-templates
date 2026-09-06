# Enterprise AI Automation Templates — the governed engine became a platform other systems call

**What:** v0.2.0 adds a versioned `/api/v1` with a committed OpenAPI contract, scoped API keys
and an OAuth2 client-credentials grant, a dashboard login, signed webhooks in both directions
with a transactional outbox, and PostgreSQL behind `docker compose` — and it makes the audit
ledger record *who was signed in* when a consequential decision was made, not only the name
that was typed into the form.

**Proof:** 148 tests (147 unit/integration plus a Playwright browser journey), green on SQLite
and again against a PostgreSQL 16 service container in CI. Four gates were each shown failing
on a planted defect before being shown green: the OpenAPI snapshot
(`docs/proof/openapi_snapshot_gate.txt`), the dependency audit
(`docs/proof/pip_audit_gate.txt` — which found a real advisory, PYSEC-2026-1845 in pytest
8.4.2, now pinned out), the secret-leak grep, and the webhook retry path
(`docs/proof/webhook_delivery_receipts.txt`: a receiver scripted 500, 500, 200 produced
`status=delivered attempts=3` with a receipt and latency per attempt). Measured from a clean
clone with no environment variables: `python -m atmpl demo up` still seeds and serves, and
`POST /api/v1/audit/verify` returned `{"valid":true,"count":25}`
(`docs/proof/clean_clone_acceptance.txt`). In Docker, a signed inbound webhook started a
governed run and four signed outbound events were verified by an independent receiver.

**Boundary:** With no `ATMPL_ADMIN_USER`/`ATMPL_ADMIN_PASSWORD_HASH` the dashboard runs in demo
mode with no login and records `human:demo` behind a visible banner, and `atmpl demo up` opens
the API without a credential — both stated in the README and printed by `atmpl doctor`. The
decision signature is a synthetic attestation, not an identity-provider signature; webhook
signing secrets are stored recoverably because the sender must reproduce them; the rate limiter
protects one process, not a fleet. Throughput and latency under load were not measured. Every
organization, person and record is invented.

**Shots:**
01-audit-names-the-authenticated-human.png — a MEDIUM stage decided through the dashboard; the
signed-decision panel now carries `Authenticated as human:mahmoud.approver` beside the actor,
role and signature, and the outbound delivery for that decision is receipted below it.
02-outbound-deliveries-with-receipts.png — the new `/webhooks` page: delivery attempts with
status, event type, destination, attempt count, HTTP code and latency, plus the subscriptions
whose signing secrets are shown once and never again.
03-versioned-api-with-openapi.png — `/api/v1/docs`, with the webhooks section highlighted; the
schema behind it is compared against a committed snapshot in CI so it cannot drift.

**LinkedIn paste:**
I shipped v0.2.0 of my governed AI automation templates: a versioned REST API with an OpenAPI
contract CI enforces, scoped API keys and OAuth2 client credentials, signed webhooks in both
directions with a transactional outbox and retries, and PostgreSQL behind Docker Compose.
The change I care about most is small: the hash-chained audit ledger now records the
authenticated principal next to the signature, so it answers *who decided* — not just what was
decided. Four safety gates were each shown failing on a planted defect before being shown
green, and the dependency audit caught a real advisory on its first run.

**Surfaces:** [ ] showcase-pdf [ ] resume [ ] website [ ] linkedin
