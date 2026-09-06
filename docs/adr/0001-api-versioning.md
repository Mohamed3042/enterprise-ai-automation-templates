# 0001 — Version the API in the path, and keep the old decision route as an alias

Status: accepted · 2026-09-05

## Context

v0.1 had a handful of dashboard routes and one JSON endpoint,
`POST /api/runs/{run_id}/stages/{stage_key}/decision`, which the HTMX forms and the README
examples both used. v0.2 turns the system into something other systems call, which means a
documented surface with a stable shape — and a promise about what happens when that shape
changes.

## Decision

- The machine API lives at `/api/v1`, mounted as its own FastAPI application, so it carries its
  own OpenAPI document at `/api/v1/openapi.json` with Swagger and ReDoc beside it. The
  dashboard keeps the root path and stays server-rendered.
- Breaking changes get `/api/v2`; `/api/v1` keeps its shape.
- `docs/openapi.v1.json` is committed and compared against the served schema in CI. Changing a
  field without regenerating the snapshot fails the build; `docs/proof/openapi_snapshot_gate.txt`
  shows that gate failing on purpose and then passing.
- Every list is cursor-paginated with an opaque cursor over `(created_at, id)`; every failure is
  `{"error": {"code", "message", "details"}}`; every response echoes `X-Request-Id`.
- The legacy `POST /api/runs/.../decision` stays, documented as a compatibility alias. It
  authenticates the way the *dashboard* does, because that is who calls it: the HTMX form on
  `/approvals`. It reaches the same engine function as the v1 route.

## Consequences

- A caller can generate a client from a schema that CI proves is current.
- Two routes now lead to one decision. They share the engine call and the audit path, so the
  duplication is in routing only — but it is duplication, and v2 should retire the alias.
- Mounting a sub-application means the error handlers are installed on both apps. That is one
  extra call, and it keeps the dashboard's HTML error rendering separate from the API envelope.
