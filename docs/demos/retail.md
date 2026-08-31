# Retail walkthrough — OmniMart Global

All data is synthetic and fake.

## Story

A multinational retailer wants one operating method for refunds and price adjustments, but
regional rules, languages, currencies, and routing differ. Three answer packs compile the
same `retail_refund.yaml` into EU, US, and GCC workflows.

`SYN-REFUND-1042` deliberately uses identical amount and return facts in EU and US. The EU
pack drafts `ROUTE_PRIVACY_REVIEW`; the US pack drafts `ROUTE_STANDARD_MANAGER`. The regional
boundary is data, not a forked codebase.

## Interview demonstration

1. Show the three answer packs and one base template.
2. Compare the two run titles and the `input_data.synthetic_case` value.
3. Show different AI routing drafts.
4. Point to MEDIUM regional review and HIGH consequential authorization.
5. Open the GCC blocked run and show G3 prompt-injection escalation.

## What this proves

- typed regional variants can adapt a template without cloning its implementation;
- policy selection is explicit and validated;
- AI classification remains a draft;
- a human authority matrix controls consequential adjustment;
- blocked content becomes operating evidence, not a hidden model error.

Regional policies in this demo are **[INFERRED]** portfolio rules, not legal advice or a
claim about the actual obligations of a retailer in any jurisdiction.

