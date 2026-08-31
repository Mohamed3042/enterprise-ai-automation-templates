# Discovery playbook

## Consulting objective

Discovery is not a requirements interview about screens. It is a controlled conversion of
an operating process into explicit ownership, scope, authority, data, regional, exception,
and evidence decisions. The output is a signed-off answer pack that the template compiler
can accept without inference.

## Engagement shape

### 1. Frame the outcome

Meet the executive sponsor and process owner together. Establish the measurable business
outcome, the current failure cost, why automation is being considered, and which decisions
must remain human. Record excluded outcomes as aggressively as included ones.

Deliverable: one paragraph of purpose, a named process owner, baseline volume, success
measures, and a scope boundary.

### 2. Walk the real process

Use three concrete cases: a normal case, a difficult but valid case, and a case that should
stop. Trace actors, handoffs, source systems, waiting time, rework, decisions, notifications,
and evidence. Separate observed behavior from official policy.

Deliverable: ordered stages, action types, inputs/outputs, escalation reasons, and SLA hints.

### 3. Resolve authority before intelligence

For every decision ask:

- Who is accountable for the outcome?
- Which role may approve at each amount, sensitivity, or impact band?
- Which role handles exceptions and absence/delegation?
- What facts must a human see before signing?
- Is rejection authority different from approval authority?
- Which action is irrevocable or externally visible?

Deliverable: an authority matrix with no unnamed role and no ambiguous amount band.

### 4. Map data and region

Classify each input field, its source, sensitivity, residency boundary, allowed recipients,
retention owner, and redaction need. Identify locale, language, accessibility, and regional
policy variations. A statement such as "we operate globally" is not an answer; each routing
boundary must resolve to an approved pack.

Deliverable: data-sensitivity level, locale list, regional variants, policy-pack ownership,
and explicit prohibited transfers.

### 5. Define AI's narrow job

Choose only draft-producing work: extract, classify, summarize, flag, recommend, or enrich.
Specify the output object, required fields, numeric bounds, forbidden content, acceptable
uncertainty, and the evidence presented to the reviewer. Never phrase a HIGH task as
"decide" or "approve".

Deliverable: adapter task contract and deterministic pre/post policy rules.

### 6. Design failure and shutdown

Ask what must happen on malformed input, prompt injection, missing evidence, model timeout,
policy conflict, role mismatch, regional mismatch, and suspected data leakage. Name the
kill-switch authority and the human work that must continue while AI is parked.

Deliverable: escalation rules, parked-state behavior, and kill-switch runbook owner.

### 7. Agree proof and change control

Define which audit fields let internal audit reconstruct a case, who verifies the chain,
which red-team scenarios are acceptance criteria, and which policy/template edits require
review. Agree that a model or prompt change is a controlled change, not a silent tweak.

Deliverable: audit tags, red-team acceptance list, approval record shape, and release owner.

## Questionnaire domains

The generated questionnaire covers these minimum domains:

1. process owner and accountable sponsor;
2. monthly volumes and meaningful peaks;
3. regional variants and approved policy packs;
4. authority matrix, amount bands, delegated roles, escalation owner;
5. risk appetite and decisions that remain human;
6. data sensitivity, prohibited fields, recipients, and retention owner;
7. locales, bilingual content, RTL/accessibility needs;
8. output schema, numeric bounds, forbidden content, and evidence;
9. SLA hints, notifications, failure handling, and kill switch;
10. audit, red-team, sign-off, and change-control expectations.

## Follow-up discipline

ATMPL treats unresolved answers as an engagement work queue, not a value to invent. The
resolver emits a numbered list with the original human question and dotted placeholder.
The consultant returns that list to the accountable owner, records the answer in YAML, and
reruns compilation. No default silently fills a material governance field.

## Exit criteria

Discovery is complete only when:

- the process owner accepts the stage order and scope;
- every placeholder validates with no unresolved token;
- every MEDIUM/HIGH stage has named approval roles;
- amount and regional bands are exhaustive and non-overlapping;
- the policy owner accepts pre/post deterministic rules;
- the kill-switch authority and parked behavior are named;
- the audit record answers who, what, when, why, and under which policy;
- red-team expectations are agreed before guardrail implementation;
- the answer pack is versioned as the source for the instantiated workflow.

**[INFERRED]** This playbook is a reusable consulting method for the portfolio system. A
real engagement would adapt workshop cadence and sign-off artifacts to the organization's
procurement, legal, risk, security, labor, records, and change-management processes.

