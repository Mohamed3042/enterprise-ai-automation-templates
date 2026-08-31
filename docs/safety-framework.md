# Safety framework: human authority by construction

ATMPL uses human-oversight, traceability, risk-tiering, intervention, and shutdown language
similar to vocabulary found in modern AI governance discussions. It is an engineering
demonstration, **not a claim of EU AI Act or any other legal/regulatory compliance**.

## G1 — risk tiers select the transition shape

- LOW: a dedicated deterministic completion function may run.
- MEDIUM: an authorized signed human decision is required after any AI draft.
- HIGH: the outcome is human-decided. AI may summarize, flag, or recommend only.

The tier is compiled into each stage, persisted, rendered in the dashboard, and tested.

## G2 — HIGH has no automatic branch

`decide_high` accepts exactly two inputs: `SignedHumanDecision` and `allowed_roles`. There
is no boolean override, environment variable, configuration flag, fallback, or overload.
Missing objects fail Pydantic validation at the API boundary with HTTP 422 and an audit
event. Objects with unauthorized roles fail before mutation.

This is the central control: the model cannot call a weaker path because that path does not
exist.

## G3 — deterministic rules surround probabilistic output

Before adapter access:

- instruction/prompt-injection patterns are quarantined;
- declared record and policy regions must match the run boundary;
- requested action must belong to stage scope;
- named sensitive fields are replaced with `[REDACTED]`.

After adapter output:

- output must be an object;
- required fields must exist;
- numeric values must remain inside compiled bounds;
- output size is bounded;
- forbidden cross-customer synthetic PII markers are rejected.

Any violation blocks the stage, escalates the run, and emits `stage_blocked`. The LLM does
not interpret or waive these rules.

## G4 — approvals are attributable

The typed decision contains actor, role, timestamp, approve/reject value, free-text reason,
and signature. The engine compares role with the compiled `allowed_roles`, persists the
decision separately from the AI draft, and emits the same fields to the audit ledger.

Dashboard signatures are deliberately labeled synthetic. **[INFERRED]** Production would
derive actor, role, and cryptographic/identity assurance from the organization's authenticated
identity and authorization systems rather than browser form values.

## G5 — evidence is append-only and tamper-evident

Each JSONL record contains its sequence, timestamp, event, subject identifiers, actor,
payload, previous hash, and its SHA-256 hash. Verification begins at a fixed genesis hash,
recomputes canonical JSON for each record, and checks both the record hash and link.

Mutation, deletion, insertion, reordering, or invalid JSON breaks verification at the first
affected line. SQLite is a dashboard read model; the JSONL chain is the proof artifact.

## G6 — human-signed kill switch

Changing the organization kill switch is itself a HIGH action and requires a signed
`risk_owner` or `chief_risk_officer` decision. When active, pending or drafted AI stages are
parked immediately. Human review/approval stages do not consult the AI switch and remain
operational. The change and rationale are audited.

## G7 — explicit adapter boundary

`mock` is the default: deterministic canned output is keyed by task and canonical input hash,
so demos and tests run offline without an API key. `claude` activates only when explicitly
selected and `ANTHROPIC_API_KEY` exists; its configured model is `claude-sonnet-5`.
`claude-fable-5` is documented as the premium option for an organization to evaluate, not
used by the demos.

## Threat-to-control matrix

| Threat | Primary controls | Expected evidence |
|---|---|---|
| Prompt injection | G3 preflight | `PROMPT_INJECTION`, stage blocked, no adapter transition |
| Unsigned HIGH API request | G2 typed boundary | HTTP 422, `approval_payload_rejected` |
| Role escalation | G4 authority check | rejected transition, actor/role in audit |
| Cross-customer output | G3 postflight | `FORBIDDEN_CONTENT`, blocked/escalated |
| Historic audit mutation | G5 verification | non-zero verify, first broken line |
| AI must stop now | G6 kill switch | AI parked, human gate remains functional |
| Malformed/oversized output | G3 postflight | schema/size/bounds violations |
| Regional routing bypass | discovery + G3 | compile or preflight refusal |

## Residual risk and production boundary

VERIFIED controls are implemented and measured in this repository. **[INFERRED]** A real
production control system additionally needs threat modeling against the actual environment,
authenticated identity claims, separation of duties, secure key custody, immutable remote
logging, incident response, model/vendor due diligence, privacy impact work, monitoring,
access reviews, retention/deletion, business continuity, and counsel-approved regional rules.

