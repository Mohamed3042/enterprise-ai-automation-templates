# Enterprise AI Automation Templates

[![CI](https://github.com/Mohamed3042/enterprise-ai-automation-templates/actions/workflows/ci.yml/badge.svg)](https://github.com/Mohamed3042/enterprise-ai-automation-templates/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-0b6b53)](https://www.python.org/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-0b6b53)](https://www.postgresql.org/)
[![Safety G1–G7](https://img.shields.io/badge/Safety-G1--G7-102b35)](docs/safety-framework.md)
[![Container](https://img.shields.io/badge/ghcr.io-enterprise--ai--automation--templates-102b35)](https://github.com/Mohamed3042/enterprise-ai-automation-templates/pkgs/container/enterprise-ai-automation-templates)
[![Evals 61/61](https://img.shields.io/badge/evals-61%2F61%20gated-0b6b53)](docs/evals.md)

> Walk into an organization, run structured discovery, fill typed placeholders, and
> ship a governed automation in days—not months. **The AI is never the final authority.**

![Live dashboard home](docs/proof/screenshots/dashboard-home.png)

This repository is a production-shaped consulting template system: the reusable asset is
not one workflow, but a method for turning client discovery into a validated workflow,
operating it behind non-bypassable guardrails, and proving every consequential transition.
**v0.2.0** let other systems drive it — a versioned REST API, scoped credentials and signed
webhooks. **v0.3.0** makes it observable and measurable: every model call is traced, costed
and shown; every provider is swappable; the red team became a scored eval suite that gates
CI; and the whole thing runs on Kubernetes and, read-only, at a public URL.

![The five-minute demo](docs/proof/demo.gif)

All organizations, people, identifiers, transactions, lessons, applications, and events
in the demos are **synthetic and fake**.

## Verify in two minutes

**A read-only public demo is built but not yet published.** `deploy/hf-space/` runs this
image on a Hugging Face Docker Space at
`https://huggingface.co/spaces/Medo4334/atmpl-governed-automation`, and CI re-deploys it on
every `main` push once an `HF_TOKEN` secret exists — but publishing it needs one interactive
`hf auth login`, so **that URL is not live yet** and this README will not pretend otherwise.
The path below is the one that is measured.

From a clean clone — this exact sequence is recorded in
[`docs/proof/clean_clone_acceptance.txt`](docs/proof/clean_clone_acceptance.txt):

```bash
git clone https://github.com/Mohamed3042/enterprise-ai-automation-templates.git
cd enterprise-ai-automation-templates
docker compose up            # api + PostgreSQL 16, migrations, seeded demos
```

Nothing but Python 3.12? The keyless path needs no Docker, no database and no API key:

```bash
python -m pip install -e ".[dev]" && python -m atmpl demo up
```

**What you will see.** The command prints, before the server starts:

```text
SEEDED: retail + ministry + bank (synthetic, deterministic, offline)
DATABASE: postgresql+psycopg://atmpl:...@db:5432/atmpl (migrations at 0001)
DASHBOARD: http://127.0.0.1:8000
API DOCS:  http://127.0.0.1:8000/api/v1/docs
MODE: demo mode - no login; decisions are recorded as human:demo
WEBHOOK IN: POST /api/v1/webhooks/helpdesk - secret whsec_...
```

Then, in the browser:

| Open | You should see |
|---|---|
| `http://127.0.0.1:8000` | Three synthetic organizations, five compiled workflows, and the invariant card: *HIGH risk is human-decided* |
| `/runs/run_bank_override` | An AI recommendation of `ROUTE_TO_TIER_2` next to a human terminal decision of **reject** |
| `/audit` → **Verify chain** | `CHAIN VERIFIED` with the record count and the last SHA-256 hash |
| `/evals` | 61 scored cases, the 24 original red-team contracts among them, with the gated pass rate |
| `/llmops` | Every model call this container made: p50/p95, tokens, estimated cost, guardrail trips |
| `/api/v1/docs` | The full OpenAPI surface — the same schema CI compares against a committed snapshot |

Two commands worth running in the same terminal:

```bash
python -m atmpl evals gate --min-pass 1.0   # GATE PASS: gated pass rate 1.000 >= required 1.000
python -m atmpl providers check             # the configured chain, and whether each has a key
```

Five minutes with an audience: [demo runbook](docs/demo-runbook.md).
Everything above also runs headless in CI on every push (badge at the top), twice — once on
SQLite and once against a PostgreSQL 16 service container.

## The problem

Enterprise AI pilots often jump from a vague use case to a model prompt. That leaves the
hard questions implicit: Who owns the process? Which region governs the record? What may
the model see? Who has authority? What happens when policy fails? How is a later reviewer
shown what happened?

ATMPL makes those questions executable:

1. **Discover** — emit the structured questions a consultant must close.
2. **Compile** — validate typed answers and resolve one base template into an org profile.
3. **Govern** — run deterministic rules before and after every model call.
4. **Decide** — keep AI output as evidence; humans own MEDIUM/HIGH transitions.
5. **Prove** — append every transition to a SHA-256 hash-chained JSONL ledger.

## One method, three demos

| Demo | Discovery claim demonstrated | Human-authority evidence |
|---|---|---|
| **OmniMart Global** | One refund template compiles into EU, US, and GCC policy packs. Identical case facts route differently at a regional boundary. | MEDIUM drafts require review; consequential adjustments are HIGH and human-decided. |
| **Ministry of Education — lesson preparation** | Bilingual Arabic + English intake remains UTF-8 end to end, including inline RTL rendering. | Department head and QA roles are resolved from the authority matrix. |
| **Gulf Horizon Bank** | AI extracts, scores completeness, flags risks, and recommends an officer route. | The AI never approves or rejects. A signed human terminal decision overrides the routing recommendation and is audited. |

Walkthroughs: [retail](docs/demos/retail.md) · [ministry](docs/demos/ministry.md) ·
[bank](docs/demos/bank.md)

## Safety is the centerpiece

| Invariant | Enforced boundary |
|---|---|
| **G1** | LOW may auto-complete. MEDIUM requires human approval. HIGH is human-decided. |
| **G2** | The HIGH transition accepts only `SignedHumanDecision`; no flag or alternate branch exists. |
| **G3** | Pure Python policy checks run before adapter access and after adapter output — and on inbound webhook payloads. Violations block and escalate. |
| **G4** | Actor, role, timestamp, decision, reason, signature **and the authenticated principal** are recorded for every approval. |
| **G5** | Append-only JSONL records embed the previous hash; verification recomputes the entire chain. |
| **G6** | A human-signed HIGH kill-switch action parks AI stages while human gates remain operational. |
| **G7** | The deterministic mock provider is default. A hosted provider is explicit, key-gated, and chosen by the deployment — never by an API caller. |

The structural boundary is deliberately small:

```python
def decide_high(
    decision: SignedHumanDecision,
    allowed_roles: list[str],
) -> DecisionOutcome:
    ...
```

There is no `auto_approve`, `allow_high`, configuration flag, fallback, or overload. See the
[safety framework](docs/safety-framework.md), the [threat model](SECURITY.md), and the
measured [red-team suite](redteam/README.md).

## See every model call

`/llmops` is fed by the same events the traces and `/metrics` are fed by — there is exactly
one path to a provider, so nothing can miss it.

| Column | What it means |
|---|---|
| calls, p50, p95, error rate | This process's calls. A fresh container says *no model calls yet* and means it. |
| tokens in / out | What the provider reported. Unreported reads *not reported*, never 0. |
| estimated cost | From [`prices.yaml`](src/atmpl/providers/prices.yaml), which carries the date it was read **and** the date it expires. An unpriced model reads *not priced*, never `$0.00`. |
| guardrail trips | How many of that provider's answers the deterministic postflight refused. |

```text
atmpl.run  (run_id, operation)
└── atmpl.stage  (stage_key, risk_tier, template_key)
    ├── atmpl.guardrail  phase=preflight   decision=allowed
    ├── atmpl.llm.call   provider · model · tokens · latency · cost_estimate
    └── atmpl.guardrail  phase=postflight  decision=allowed
```

A blocked preflight produces **no** `atmpl.llm.call` child, and that absence is the evidence
the payload never reached a model. `ATMPL_TRACE_EXPORTER=otlp docker compose --profile tracing
up` sends the same spans to a real Jaeger:

![One governed run in Jaeger](docs/proof/screenshots/jaeger-trace.png)

## Providers, and what is actually verified

| Provider | Structured output | Status |
|---|---|---|
| `mock` | deterministic JSON | Default. Keyless, offline, byte-identical run to run. |
| `gemini` | `responseSchema` | **Live-verified** against `gemini-3.6-flash` from this repository, and in CI wherever the secret exists. |
| `anthropic` | forced tool call | **Contract-tested** against recorded responses — no key exists on the build machine. |
| `openai` (also Azure, OpenRouter, Together, vLLM, Ollama) | `response_format: json_schema` | **Contract-tested** against recorded responses. |

That is the honest sentence, and it is deliberately not "supports all major LLM providers".
The recorded bodies, and what they are and are not, are in
[`tests/cassettes/README.md`](tests/cassettes/README.md).

Around all four: retries with backoff on retryable errors only, a fallback chain, a circuit
breaker, a timeout, and one ledger row plus one span per attempt. Two things the live API
taught this code, both in [ADR 0007](docs/adr/0007-provider-router.md):

- `gemini-2.0-flash` and `gemini-2.5-flash` are gone for new API users — the API says so in
  the 400 it returns;
- a thinking model bills its thoughts. One measured answer of **61 tokens carried 1,844
  reasoning tokens**; counting only the answer under-reports the cost thirtyfold.

## Discovery, done by an agent

```bash
python -m atmpl discover --text "A European retail chain handles about 4,000 refund requests \
a month. A store manager approves refunds up to 250 EUR; the regional director approves \
anything above and owns escalations. Order records are confidential."
```

A PydanticAI agent turns that paragraph into the template's **own** typed questionnaire, with
a reason and a `stated | inferred` confidence per field, and the open questions it declined to
guess. It runs on the deployment's provider router, so its calls are on `/llmops` like any
other. It is held to two boundaries that are enforced rather than requested:

1. a field the template does not declare is a **422 naming the field**, never a merge;
2. it creates nothing and decides nothing — a human edits the draft and calls `resolve`.

`POST /api/v1/discovery/agent` is the same thing over HTTP. [ADR 0008](docs/adr/0008-pydanticai-discovery-agent.md)
records what the schema could not express and what was measured instead.

## Integrate

Everything the dashboard does, another system can do — with a credential and a scope.

```bash
# 1. Mint a scoped key. It is printed once and stored as a salted digest.
atmpl keys create --name acme-erp --scopes runs:read,runs:write,decisions:write
#    -> key: atmpl_9f3c...   (or use OAuth2: POST /api/v1/oauth/token, HS256, <= 15 min)

# 2. Start a governed run. The caller does not get to choose the model adapter.
curl -X POST http://127.0.0.1:8000/api/v1/runs \
  -H "Authorization: Bearer $ATMPL_KEY" -H "content-type: application/json" \
  -d '{"workflow_id":"wf_bank_triage","title":"SYN-LOAN-7000 via API"}'

# 3. Record a signed human decision. The authenticated principal is bound into the ledger.
curl -X POST "http://127.0.0.1:8000/api/v1/runs/$RUN/stages/terminal_decision/decision" \
  -H "Authorization: Bearer $ATMPL_KEY" -H "content-type: application/json" \
  -d '{"actor":"Mariam Synthetic","role":"credit_officer_tier_2","decision":"reject",
       "reason":"Synthetic evidence does not meet the human credit standard.",
       "signature":"sig_api_001"}'

# 4. Subscribe, and be told what the human decided. The secret is shown once.
curl -X POST http://127.0.0.1:8000/api/v1/webhooks/subscriptions \
  -H "Authorization: Bearer $ATMPL_KEY" -H "content-type: application/json" \
  -d '{"url":"https://example.invalid/hook","event_types":["run.stage.decided"]}'
```

Deliveries carry `X-ATMPL-Signature: t=<unix>,v1=<hmac-sha256>` over `<t>.<body>`; a 15-line
verifier, the inbound direction, the retry and dead-letter behaviour, and the honest
at-least-once boundary are all in [docs/webhooks.md](docs/webhooks.md).

**Demo mode, stated plainly.** With no `ATMPL_ADMIN_USER` and `ATMPL_ADMIN_PASSWORD_HASH`, the
dashboard has no login and records decisions as `human:demo` behind a visible banner, and
`atmpl demo up` additionally sets `ATMPL_DEMO_OPEN_API=1` so the API answers without a
credential. Set those two variables and the dashboard requires a sign-in, the API requires a
credential, and the ledger names the human who was actually connected. `atmpl doctor` prints
which of the two you are running, and every variable is documented in
[`.env.example`](.env.example).

## Architecture

```mermaid
flowchart LR
    C[Consultant / process owner] -->|questionnaire + answers| D[Discovery resolver]
    T[(Base YAML templates<br/>Pydantic schemas)] --> D
    D -->|validated org profile| W[Instantiated workflow]
    EV[External event<br/>signed inbound webhook] --> W
    W --> E[Execution engine]
    E --> PRE[Deterministic pre-policy G3]
    PRE --> A{Provider router — chain chosen by settings G7}
    A -->|offline default| M[Deterministic mock]
    A -->|key required| CL[Gemini · Anthropic · OpenAI-compatible]
    A --> OBS[(Spans · /metrics · call ledger)]
    M --> POST[Deterministic post-policy G3]
    CL --> POST
    POST -->|draft only| H[Human approval boundary G1/G2/G4]
    H --> S[(PostgreSQL or SQLite)]
    E --> L[(Append-only JSONL<br/>SHA-256 chain G5)]
    H --> L
    H --> OB[(Transactional outbox)]
    OB --> WH[Signed outbound delivery<br/>retries · receipts · dead-letter]
    S --> UI[FastAPI dashboard + /api/v1]
    L --> UI
```

```mermaid
sequenceDiagram
    participant U as Submitted record
    participant P as Deterministic policy
    participant A as LLM adapter
    participant E as Execution engine
    participant H as Authorized human
    participant L as Hash-chained audit
    U->>P: Input + declared region/scope
    P-->>L: Preflight result
    alt Policy violation
        P-->>E: BLOCK + escalate
        E-->>L: stage_blocked
    else Allowed
        P->>A: Redacted, scoped input
        A-->>P: Draft / flags / recommendation
        P->>E: Schema-validated draft
        E-->>L: ai_draft_created (no business transition)
        E->>H: HIGH gate + evidence
        H->>E: SignedHumanDecision + authenticated principal
        E->>E: Verify role against authority matrix
        E-->>L: human_decision_recorded
    end
```

C4 context and container diagrams, the trust-boundary table, and the outbox sequence:
[architecture](docs/architecture.md) · [template anatomy](docs/template-anatomy.md) ·
[discovery playbook](docs/discovery-playbook.md) · decisions:
[0001 API versioning](docs/adr/0001-api-versioning.md),
[0002 auth model](docs/adr/0002-auth-model.md),
[0003 webhook outbox](docs/adr/0003-webhook-outbox.md),
[0004 SQLite and PostgreSQL](docs/adr/0004-sqlite-and-postgres.md),
[0005 secrets provider](docs/adr/0005-secrets-provider.md),
[0006 telemetry](docs/adr/0006-telemetry.md),
[0007 provider router](docs/adr/0007-provider-router.md),
[0008 the discovery agent](docs/adr/0008-pydanticai-discovery-agent.md),
[0009 evals as a CI gate](docs/adr/0009-evals-as-ci-gate.md),
[0010 Kubernetes and the Space](docs/adr/0010-kubernetes-and-space.md).

Operations: [evals methodology](docs/evals.md) · [running it on Kubernetes](docs/deploy-k8s.md).

## Screenshot evidence

Every image below was captured with Playwright from the live seeded application running under
`docker compose` — not from static or mocked HTML.

| Dashboard | Human override |
|---|---|
| ![Dashboard home](docs/proof/screenshots/dashboard-home.png) | ![Bank human override](docs/proof/screenshots/bank-human-override.png) |
| **Arabic rendering** | **Approval queue** |
| ![Ministry Arabic workflow](docs/proof/screenshots/ministry-arabic.png) | ![Pending approvals](docs/proof/screenshots/pending-approvals.png) |
| **Audit verification** | **Scored evals** |
| ![Audit chain verified](docs/proof/screenshots/audit-verify.png) | ![Eval report](docs/proof/screenshots/evals-report.png) |
| **Every model call** | **The same run in Jaeger** |
| ![LLMOps page](docs/proof/screenshots/llmops-page.png) | ![Jaeger trace](docs/proof/screenshots/jaeger-trace.png) |
| **A run started by a signed webhook** | **Outbound deliveries with receipts** |
| ![Webhook-started run](docs/proof/screenshots/webhook-started-run.png) | ![Webhook deliveries](docs/proof/screenshots/webhook-deliveries.png) |
| **The API a caller reads** | |
| ![API docs](docs/proof/screenshots/api-docs.png) | |

## CLI contract

```text
init <template> --org <name>       emit questionnaire.md + answers.yaml
resolve <template> <answers>       validate and compile an instantiated workflow
run <workflow>                     execute until the next governed human gate
demo up [--exercise]               seed all demos, migrate, and serve the dashboard
redteam                            execute R1–R8 across all three demos
evals run [--provider gemini]      score every case and write a JSON + Markdown report
evals gate --min-pass 1.0          fail the build when a gated category slips
discover --text "<a process>"      draft a typed questionnaire from a description
providers check [--ping]           the configured chain, its keys, and a live ping
audit verify                       recompute the JSONL hash chain
db upgrade | db current            apply Alembic migrations for ATMPL_DATABASE_URL
keys create | list | revoke        scoped API keys for machine callers
clients create                     an OAuth2 client-credentials principal
users hash-password                a PBKDF2 hash for ATMPL_ADMIN_PASSWORD_HASH
webhooks deliver | sources         one delivery pass; the configured inbound sources
doctor                             what this deployment is actually configured as
```

Invalid discovery answers fail with a numbered consultant follow-up list. AI stages attach
drafts but never move the business process by themselves. The dashboard, the v1 API and the
tests all call the same engine function, so none of the three can drift into its own rules.

## What is real vs simulated

- **VERIFIED:** The template resolver, Pydantic validation, PostgreSQL and SQLite persistence,
  Alembic migrations, the `/api/v1` surface with its committed OpenAPI snapshot, scoped API
  keys, the OAuth2 client-credentials grant, the dashboard login and CSRF, signed inbound and
  outbound webhooks with retries and receipts, the deterministic policy checks, the audit hash
  chain, the kill switch, the Docker image, the OpenTelemetry span tree, the Prometheus
  collectors, the provider router with its retries and circuit breaker, the eval suite and its
  gate, the Kubernetes manifests (rendered, schema-validated and stood up on kind in CI),
  **233 tests** including a Playwright browser journey, and every screenshot above. Evidence
  is in [`docs/proof/`](docs/proof/).
- **VERIFIED, live:** Gemini `gemini-3.6-flash` answered in the declared JSON schema, and the
  discovery agent produced a valid retail questionnaire from a paragraph — in three attempts
  across 58 seconds, one of them a retried 5xx. Both are `pytest -m live`, skipped without a
  key.
- **VERIFIED:** The red-team contracts were committed and measured failing before the
  guardrails were wired, then measured passing afterward. The OpenAPI snapshot gate, the
  dependency-audit gate and the secret-leak gate were each shown failing on a planted defect
  before being shown green.
- **[INFERRED]:** The discovery ordering, SLA hints, authority roles, sample regional rules,
  the event vocabulary and the inbound field mappings are design choices for this portfolio
  implementation — not client policy.
- **ESTIMATED, never billed:** every cost figure comes from a committed price table with the
  date it was read on it. Discounts, batch tiers, cached-input rates and free tiers are not
  modelled, and a model the table does not know is reported as *not priced*.
- **PER PROCESS, not persisted:** the `/llmops` model-call table is an in-memory ring buffer
  and empties on restart. The per-template approvals and blocks beside it come from the
  database and do not. The page says which is which.
- **SIMULATED:** Every demo organization and record is fake. The mock adapter produces
  deterministic canned drafts. Dashboard signatures are synthetic attestations, not an
  enterprise identity-provider signature.
- **NOT MEASURED:** Throughput, concurrency limits and latency under load. No number is claimed
  for them. Nor is there a model-judged quality score: [docs/evals.md](docs/evals.md) says what
  would have to be true before one could be published.
- **DOCUMENTED LIMITS, measured every run:** the deterministic injection filter matches the
  phrasings it knows and not their paraphrases, and credential keys are not redacted. Both are
  eval cases (`E-INJ-05`, `E-PII-02`) that expect `allowed`, so the boundary is a number in
  the report rather than a footnote.
- **NOT CLAIMED:** This repository does not claim legal, regulatory, security, or EU AI Act
  compliance. It demonstrates human-oversight vocabulary and engineering controls that a
  real deployment would map to counsel-approved policy, authentication, authorization,
  retention, monitoring, and change control. What is deliberately *not* protected is listed
  in [SECURITY.md](SECURITY.md).

## Proof and CI

CI runs Ruff, the full suite on SQLite **and** on PostgreSQL 16, `python -m atmpl redteam`,
the scored eval suite behind `evals gate --min-pass 1.0`, `pip-audit`, a Playwright end-to-end
journey, `kubeconform` over both Kubernetes overlays followed by a **kind** cluster that
applies `overlays/dev` and curls `/ready`, `/metrics` and `/llmops`, a live Gemini job where
the secret exists, a Docker build smoke-tested and published to GHCR from `main`, and the
Hugging Face Space upload.

Every new gate was shown RED with its fix removed before being shown GREEN with it in place:

- [The span tree of one governed run](docs/proof/telemetry_span_gate.txt) — one guardrail span
  swapped for a stage span; the count stays at five and the shape assertion still catches it
- [The evals gate](docs/proof/evals_gate.txt) — one expectation flipped: `0.984 < 1.000`
- [The Kubernetes security posture](docs/proof/k8s_manifest_gate.txt)
- [An unpriced model is not priced at zero](docs/proof/cost_honesty_gate.txt)
- [The agent may not widen the questionnaire](docs/proof/agent_invented_field_gate.txt)
- [An empty list variable is empty, not a start-up crash](docs/proof/settings_empty_list_gate.txt)
  — found by the kind job, not by a test: `ATMPL_PROVIDER_FALLBACKS: ""` is what a ConfigMap
  passes for "no fallbacks", and pydantic-settings JSON-decoded it before any validator ran
- [Full pytest output](docs/proof/pytest_full.txt)
- [Clean-clone acceptance](docs/proof/clean_clone_acceptance.txt)
- [RED-before evidence](docs/proof/redteam_red_before.txt) ·
  [GREEN-after evidence](docs/proof/redteam_green_after.txt)
- [OpenAPI snapshot gate, shown failing first](docs/proof/openapi_snapshot_gate.txt)
- [Dependency audit gate, and the real vulnerability it caught](docs/proof/pip_audit_gate.txt)
- [Measured webhook delivery receipts](docs/proof/webhook_delivery_receipts.txt)
- [Live screenshot capture script](scripts/capture_screenshots.py) ·
  [demo recorder](scripts/record_demo.py) ·
  [the recorded demo](docs/proof/demo.mp4)

MIT licensed. Built as a public portfolio demonstration by Mohamed Mahmoud.
