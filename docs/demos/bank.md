# Bank walkthrough — Gulf Horizon Bank loan triage

Gulf Horizon Bank, every person, every application, every amount, and every decision is
synthetic and fake.

## Story

The AI extracts a synthetic application, scores completeness, flags risk factors, drafts a
triage summary, and recommends an officer route. It never approves or rejects credit.
Amount bands map to officer tiers, and every terminal decision is a HIGH signed human stage.

The money-shot run is `SYN-LOAN-2048`. The AI recommends `ROUTE_TO_TIER_2`. The Tier 2
human reviews other synthetic evidence and records `REJECT` with an override rationale.
`human_override_recorded` captures the AI recommendation, terminal human decision, reason,
actor, and synthetic signature in the hash-chained audit.

![Human override](../proof/screenshots/bank-human-override.png)

## Interview demonstration

1. Open the override run and compare the two evidence panels.
2. Point out that `ROUTE_TO_TIER_2` is routing, not approval.
3. Show the HIGH terminal stage's actor, role, reason, and signature.
4. Open the audit ledger and filter `human_override_recorded`.
5. Open the pending run and submit a synthetic human decision through the HTMX form.
6. Explain that the form and JSON API call the same engine method.

## What this proves

- structured AI assistance can accelerate triage without delegating authority;
- amount-band authority is explicit client discovery data;
- draft and decision are separate persistent records;
- a human can override the AI's routing recommendation;
- the reason and override survive as tamper-evident evidence.

