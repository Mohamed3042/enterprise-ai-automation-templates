# Architecture

## Design objective

ATMPL separates adaptation, generation, authority, and evidence. A client-specific workflow
is compiled from a reusable template and typed discovery answers. AI is one draft-producing
component inside that workflow; it is not the workflow controller or policy authority.

## Boundaries

| Boundary | Owns | Deliberately does not own |
|---|---|---|
| Template catalog | Ordered stages, risk tiers, roles, inputs, outputs, escalation, SLA hints, audit tags | Organization facts |
| Discovery resolver | Questions, typed answer validation, placeholder resolution, regional consistency | Guessing missing client answers |
| Execution engine | Persistent stage state, allowed transitions, adapter calls, audit emission | Final HIGH decisions |
| Policy engine | Redaction, scope/region checks, output schema, forbidden content, numeric bounds | Probabilistic judgment |
| LLM adapter | Drafts, extraction, flags, routing recommendations | Business transitions or approvals |
| Approval boundary | Signed human decision and authority-matrix check | Model-generated signatures |
| Audit ledger | Append-only ordered evidence and tamper detection | Data-retention policy for a real client |
| Dashboard | Read models and calls to the shared engine mutation API | Dashboard-only approval logic |

## Data flow

1. `atmpl init` reflects over the selected Pydantic placeholder model and emits a Markdown
   questionnaire plus a nested YAML answer file.
2. `atmpl resolve` validates the YAML with `extra="forbid"`. Missing, empty, unknown, or
   type-invalid values become a numbered follow-up list.
3. The resolver substitutes `{{dotted.names}}`. Exact placeholders preserve their typed
   value; placeholders embedded in prose become strings.
4. The instantiated spec is persisted alongside its organization profile.
5. The engine creates ordered stage records. LOW deterministic actions have a dedicated
   completion function. AI actions pass through pre-policy, adapter, and post-policy, then
   attach a draft with `business_transition: false` in the audit event.
6. MEDIUM/HIGH decisions require `SignedHumanDecision`; role is checked against the stage's
   compiled authority matrix before state changes.
7. Every transition appends a JSONL record and mirrors it to SQLite for the dashboard.

## Persistence model

- `organizations`: profile, sector, organization kill switch.
- `workflows`: immutable compiled specification snapshot.
- `runs`: one workflow execution and its declared region.
- `stages`: action, risk, status, validated input, AI draft, human decision, escalation.
- `audit_events`: SQLite read model of the append-only JSONL event.
- `redteam_results`: seeded display of the executable R1–R8 outcomes.

SQLite is appropriate for a deterministic portfolio demonstration and local consultant
prototype. **[INFERRED]** A production deployment would normally use a managed transactional
database, object-locked audit sink, authenticated identity claims, secrets management,
concurrency controls, metrics, backup/restore, and documented retention.

## Failure behavior

The system fails closed:

- invalid discovery → no workflow;
- regional mismatch → no compilation or adapter call;
- prompt injection/scope failure → stage blocked and escalated;
- missing/malformed/oversized adapter output → stage blocked and escalated;
- unsigned HIGH request → HTTP 422 plus audit event;
- unauthorized role → rejected transition plus audit event;
- active kill switch → AI stage parked; human gates remain usable;
- audit mutation → verification returns non-zero and names the first broken line.

