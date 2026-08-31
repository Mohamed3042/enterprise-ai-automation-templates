# Template anatomy

A base template is a sector-neutral operating contract expressed as YAML. It declares
ordered stages and the evidence each stage consumes or creates. Organization facts stay in
typed discovery answers, not copied into code.

```yaml
metadata:
  id: loan_triage
  name: "{{organization.name}} — human-authority loan triage"
  version: "1.0.0"
  sector: banking
  description: AI drafts; a signed human owns the terminal credit decision.
actors:
  - role: "{{credit.terminal_decision_role}}"
    responsibility: Own every terminal approve or reject decision.
stages:
  - id: credit_summary
    name: Draft triage recommendation for human review
    action_type: ai_draft
    risk_tier: HIGH
    inputs:
      authority_bands: "{{credit.authority_bands}}"
      allowed_actions: [summarize_application]
    outputs:
      required: [summary, recommendation, completeness_score, risk_flags]
      numeric_bounds:
        completeness_score: [0, 100]
    escalation_rules: [risk_flag_requires_senior_officer]
    sla_hours: 4
    audit_tags: [bank, high_risk, ai_recommendation_only]
    approval_roles: ["{{credit.terminal_decision_role}}"]
```

## Required stage fields

| Field | Purpose |
|---|---|
| `action_type` | Constrains behavior to draft, extract, classify, review, approve, system action, or notify. |
| `risk_tier` | Selects the allowed transition shape; HIGH can only receive a signed human decision. |
| `inputs` | Declares scope and resolved organization policy used by deterministic preflight. |
| `outputs` | Defines required output keys and numeric bounds for deterministic postflight. |
| `escalation_rules` | Makes exceptional routing explicit before execution. |
| `sla_hours` | Gives the operating team a review target; it is a hint, not an enforcement clock here. |
| `audit_tags` | Makes later evidence filtering possible. |
| `approval_roles` | Compiles the authority boundary into the stage record. |

## Typed placeholders

Every base YAML has a committed `placeholders.schema.json` generated from a Pydantic v2
model. Leaf fields carry `question` metadata so the schema is also the consultant's
"ask the right questions" artifact. Enums become allowed values, numbers carry bounds,
and nested models produce dotted names such as `regional.policy_pack`.

Run `scripts/generate_schemas.py` after changing a placeholder model. Tests compare each
committed schema to live `model_json_schema()` output so schema drift fails CI.

## Resolution rules

- Exact `"{{path}}"` replacement preserves lists, maps, numbers, and booleans.
- Embedded replacement produces a human-readable string.
- Missing, null, empty list, empty map, wrong type, out-of-range value, or extra field fails.
- EU/US/GCC retail policy packs are paired deterministically; crossing a boundary fails.
- The compiled workflow stores its source template and complete validated org profile.

