# Security policy

This repository is a synthetic portfolio demonstration of governed AI automation. Every
organization, person, record, and event in its demos is fake. **Do not submit real personal,
financial, education, employee, customer, or company-confidential data to it.**

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository. Please do not open a public
issue first, and never include real sensitive data in a report, pull request, fixture,
screenshot, or audit log.

## Threat model

The asset worth protecting here is **the integrity of a consequential decision and its
record**: who decided, on what evidence, under which policy, and whether the log can be
altered afterwards. The controls below exist for that, in this order.

| Threat | Control | Where |
|---|---|---|
| An AI output is treated as a decision | `decide_high` accepts only a `SignedHumanDecision`; no flag, overload or alternate branch exists (**G2**) | `src/atmpl/guardrails/decisions.py` |
| Someone approves outside their authority | The role is checked against the stage's authority matrix; refusals are audited (**G4**) | `guardrails/decisions.py`, `engine/service.py` |
| A caller impersonates a human decision | Every request resolves to a principal (`human:<user>` / `client:<id>`), and the principal is written into the hash-chained record beside the signature | `security/dependencies.py`, `engine/service.py` |
| A stolen credential does more than its job | Scoped API keys and OAuth2 access tokens; each route requires one scope | `security/principals.py`, `security/credentials.py` |
| A credential leaks from the database | Only a salted SHA-256 digest of a key or client secret is stored; passwords use PBKDF2-HMAC-SHA256 (600,000 iterations) | `security/credentials.py` |
| A token outlives its purpose, or is signed with a retired key | Access tokens are HS256, expire in ≤ 15 minutes, and carry a `kid`; an unknown `kid` is refused before any signature check | `security/tokens.py` |
| A browser is tricked into approving something | Session cookies are `HttpOnly`, `SameSite=Lax` (and `Secure` when HSTS is on) and HMAC-signed; form POSTs carry a CSRF token bound to the session | `web/app.py`, `security/tokens.py` |
| A forged event starts a governed run | Inbound webhooks require `X-ATMPL-Signature` (HMAC-SHA256 over `<timestamp>.<body>`) inside a five-minute tolerance, and an idempotency key makes a replay a no-op | `webhooks/signing.py`, `api/routers/webhooks.py` |
| A caller chooses the model, the workflow, or the region | The adapter is a deployment setting (**G7**); inbound mappings are declared in the organization profile, never in the request | `settings.py`, `webhooks/inbound.py` |
| Prompt injection or cross-customer content | Deterministic pure-Python policy runs before and after every model call, and again on inbound webhook payloads (**G3**) | `guardrails/policy.py` |
| The audit trail is edited after the fact | Append-only JSONL, each record embedding the previous hash; `atmpl audit verify` recomputes the whole chain (**G5**) | `audit.py` |
| Secrets end up in the log or the ledger | All secret material comes from a provider (`env` or a 0600 JSON file), and a test greps the written ledger, the captured logs and the rendered audit page for every configured secret | `secrets/`, `tests/test_secrets.py` |
| A vulnerable dependency ships | `pip-audit` runs in CI; the gate is shown failing first in `docs/proof/pip_audit_gate.txt` | `.github/workflows/ci.yml` |
| Abuse or oversized bodies | Per-credential token-bucket rate limit (429 with `Retry-After`) and a request body cap (413) | `security/middleware.py` |
| Clickjacking, sniffing, referrer leakage | CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `X-Frame-Options`, optional HSTS | `security/middleware.py` |

## What is deliberately NOT protected

Naming these is the point of the section; each is an integration boundary, not an oversight.

- **Demo mode.** With no `ATMPL_ADMIN_USER`/`ATMPL_ADMIN_PASSWORD_HASH`, the dashboard has no
  login and records decisions as `human:demo`. `python -m atmpl demo up` additionally sets
  `ATMPL_DEMO_OPEN_API=1` so the API answers without a credential. Both are announced in the UI
  and by `atmpl doctor`. Neither is suitable for anything but a demo.
- **Ephemeral signing keys.** Without `ATMPL_SECRET_JWT_SIGNING_KEY` and
  `ATMPL_SECRET_SESSION_SIGNING_KEY`, keys are random per process, so every restart invalidates
  outstanding tokens and sessions. `atmpl doctor` prints `EPHEMERAL` when that is the case.
- **Recoverable webhook secrets.** Outbound subscription secrets and inbound source secrets are
  stored so this service can reproduce a signature. They are shown once, masked afterwards, and
  never logged — but they are recoverable from the database by anyone with database access. A
  production deployment holds them in a KMS or the secrets provider (`webhook_inbound_<source>`
  already overrides the stored value).
- **Single-process rate limiting.** The token bucket protects one container, not a fleet. A
  multi-replica deployment needs a shared store.
- **Synthetic signatures.** `SignedHumanDecision.signature` is an attestation string, not a
  cryptographic signature from an enterprise identity provider.
- **One local admin.** There is no user directory, SSO, MFA, or role hierarchy for humans; a
  real deployment maps the principal to its own IdP.
- **No compliance claim.** This repository does not claim legal, regulatory, security, or
  EU AI Act compliance. It demonstrates the engineering controls a real deployment would map
  to counsel-approved policy, authentication, retention, monitoring, and change control.

## Reporting a security problem in the demo data

There is none to report: the data is invented. If you find a real identifier anywhere in this
repository, that is a bug — report it privately and it will be removed.
