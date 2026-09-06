# 0005 — One secrets interface, an honest ephemeral state, and a leak gate that can fail

Status: accepted · 2026-09-05

## Context

v0.2 introduces material that must never appear in a log, a screenshot, the repository, or —
especially — the audit ledger this project asks a reader to trust: JWT and session signing keys,
API keys, OAuth client secrets, and webhook secrets.

## Decision

- All secret reads go through a `SecretsProvider`: `env` (`ATMPL_SECRET_<NAME>`) by default, or
  `file` (a JSON object whose mode must not be group- or world-readable on POSIX). Adding a
  vault backend is one class.
- Nothing has a hardcoded fallback. Where the keyless demo needs a key to exist at all, the
  resolver mints a **process-lifetime random** one and says so out loud: `atmpl doctor` prints
  `EPHEMERAL (restarts invalidate tokens)`, and the dashboard banner names demo mode.
- Credentials are stored as salted digests, never recoverably (ADR 0002). The two exceptions —
  webhook signing secrets, which the sender must reproduce — are named in `SECURITY.md` with
  the reason, shown once, masked afterwards, and overridable from the provider.
- A test exercises login, an API key, a token grant and a run creation, then greps the written
  ledger, the captured logs and the rendered audit page for every configured secret value. A
  sibling test plants a secret in the ledger and asserts the same check finds it, so the gate is
  known to be capable of failing.

## Alternatives considered

- *Read `os.environ` directly at each call site.* Fewer lines, and no seam for a vault, no place
  to express "this key is ephemeral", and nothing to enumerate for the leak gate.
- *Refuse to start without configured keys.* Safer for a server, fatal for the front door. The
  compromise is to run and to say what it is running as.

## Consequences

- A deployment that forgets its keys still runs, and still tells the operator it is degraded,
  rather than either crashing or silently pretending to be configured.
- The leak gate greps for literal values, so it cannot catch a secret that is transformed before
  it leaks (base64, truncation, a hash prefix). It catches the mistake people actually make:
  putting the value in a payload or a log line.
