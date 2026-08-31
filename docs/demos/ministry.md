# Ministry walkthrough — bilingual lesson preparation

All ministry records, people, curricula, schools, and plans are synthetic and fake.

## Story

A teacher submits a plan. AI produces an enrichment/alignment draft. A department head owns
the HIGH alignment approval, QA owns a separate MEDIUM gate, and only then may the LOW
publish action run.

The seeded run `خطة درس الرياضيات — الصف الخامس` includes Arabic and English lesson title,
subject, and a clearly synthetic Arabic teacher name. Data stays UTF-8 from YAML through
Pydantic, SQLite JSON, Jinja, and HTML. Evidence panels use `dir="auto"` so Arabic renders
RTL inline without forcing the surrounding English interface to RTL.

## Interview demonstration

1. Open the Arabic run from the portfolio dashboard.
2. Read the bilingual validated input and enrichment draft.
3. Show that the AI stage stops at `draft_ready`.
4. Identify department-head and QA roles compiled from discovery.
5. Compare the completed English science run's separate decisions.

## What this proves

- language/locale requirements belong in discovery;
- Unicode is tested through the complete runtime path;
- content enrichment and curriculum authority remain separate;
- review and QA are distinct accountable gates;
- publication cannot convert an AI draft into authority.

