# 0002 — Two credential kinds for machines, one session for humans, scopes for both

Status: accepted · 2026-09-05

## Context

The repository's whole claim is that a human owns consequential decisions. Before v0.2 the
ledger recorded the *typed* actor of a decision and nothing about who was actually connected —
anyone who could reach the port could type any name. The claim needed a subject.

## Decision

- **Principals.** Every request resolves to a `Principal`: `human:<user>` from a dashboard
  session, or `client:<id>` from a machine credential. The principal is written into the
  hash-chained record next to the signature, so the ledger answers *who decided*.
- **API keys** for the ordinary integration case: `atmpl keys create --name … --scopes …`
  prints the key once and stores a salted SHA-256 digest, with a prefix column for lookup, a
  last-used timestamp, and revocation.
- **OAuth2 client-credentials** for callers that expect it: `POST /api/v1/oauth/token` returns
  an HS256 JWT with a `scope` claim, `exp` of at most 15 minutes, and a `kid` header. An unknown
  `kid` is refused *before* any signature check, so key rotation is a real operation.
- **Scopes** (`runs:read`, `decisions:write`, `webhooks:manage`, …) are enforced per route by a
  dependency, and a denial names both what was required and what was granted.
- **Humans** get a login with an HMAC-signed `HttpOnly`, `SameSite=Lax` cookie and a CSRF token
  on form POSTs. Passwords are PBKDF2-HMAC-SHA256 (600,000 iterations) from the standard
  library — no new dependency, as the packet required.
- **Demo mode.** With no `ATMPL_ADMIN_USER`/`ATMPL_ADMIN_PASSWORD_HASH`, the dashboard has no
  login and records `human:demo` behind a visible banner; the API still requires a credential
  unless `ATMPL_DEMO_OPEN_API=1`, which `atmpl demo up` sets and the README explains.

## Alternatives considered

- *API keys only.* Simpler, but the client-credentials grant is what an integration team asks
  for by name, and it is about forty lines once principals exist.
- *argon2 via `argon2-cffi`.* Better hashing, and a compiled dependency for one password in a
  demo. PBKDF2 at 600,000 iterations is the standard-library answer; a real deployment
  delegates to its identity provider anyway.
- *Cryptographically signed decisions from a real IdP.* Correct, and out of scope.
  `SECURITY.md` says plainly that the signature is an attestation string, not an identity.

## Consequences

- The rate limiter is per credential and lives in one process; a multi-replica deployment needs
  a shared store, and `SECURITY.md` says so rather than leaving it implied.
- Without a configured `jwt_signing_key`, tokens are signed with a process-lifetime random key
  and die on restart. That is right for a keyless demo and wrong for a deployment, so
  `atmpl doctor` prints `EPHEMERAL` when it is the case.
- CSRF is enforced on form-encoded mutations while a session cookie is present. JSON callers
  are not checked, because a cross-origin JSON POST needs a CORS preflight this service does not
  grant by default.
