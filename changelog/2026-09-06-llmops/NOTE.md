# Enterprise AI Automation Templates — every model call is now traced, costed and scored

**What:** v0.3.0 makes the governed engine observable and measurable. OpenTelemetry traces a
run as `atmpl.run → atmpl.stage → atmpl.guardrail | atmpl.llm.call`; Prometheus counters serve
at `/metrics`; a new `/llmops` page shows every model call this process made — p50/p95, tokens
in and out, an estimated cost, guardrail trips and error rate per provider — beside the
approvals-against-blocks the database holds. A provider router puts Gemini, Anthropic and any
OpenAI-compatible endpoint behind one interface with retries, a fallback chain, a circuit
breaker and a committed price table, with the deterministic mock still the keyless default. A
PydanticAI discovery agent turns a paragraph about a process into the template's own typed
questionnaire, with a reason per field. The R1–R8 red team became **61 scored eval cases**
behind a CI gate. And the whole thing now has Kubernetes manifests smoke-tested on kind in CI,
and a read-only public demo.

**Proof:** 245 tests green, plus 3 live tests against `gemini-3.6-flash` that are skipped
without a key. Five new gates were each shown RED with the fix removed and GREEN with it back:
the span tree (`docs/proof/telemetry_span_gate.txt` — one guardrail span swapped for a stage
span, span count unchanged at five, and the shape assertion still catches it), the evals gate
(`docs/proof/evals_gate.txt` — one expectation flipped: `GATE FAIL: gated pass rate 0.984 <
required 1.000`), the Kubernetes security posture, the "an unpriced model is not priced at
zero" rule, and the agent's refusal to widen the questionnaire. The container was built and run
read-only: 61 cases scored *inside it*, `/llmops`, `/evals` and `/metrics` all 200, and a POST
refused with `403 demo_readonly`. A real Jaeger received the spans
(`docs/proof/screenshots/jaeger-trace.png`: 5 spans, depth 3, 14.93 ms).

**Three things the live API taught the code, none of which were assumptions:**
`gemini-2.0-flash` and `gemini-2.5-flash` are gone for new API users — the API says so in the
400 it returns. A thinking model bills its thoughts: one measured answer of **61 tokens carried
1,844 reasoning tokens**, so counting only `candidatesTokenCount` under-reports the cost
thirtyfold. And a `dict[str, str]` field is expressible in JSON Schema but not in any
provider's structured-output mode — asked for a free-form object, `gemini-3.6-flash` returned
`{}` every time, so authority maps travel as key/value rows and are rebuilt before validation.

**Boundary:** Gemini is live-verified; the Anthropic and OpenAI-compatible adapters are
**contract-tested against recorded responses**, because no key for either exists on the machine
this was built on — the README says exactly that rather than "supports all major providers".
Every cost figure is an **estimate** from a price table carrying the date it was read and the
date it expires; a model that table does not know reads *not priced*, never `$0.00`. The
`/llmops` call table is an in-memory ring buffer and empties on restart — the page says so, and
the per-template counts beside it come from the database and do not. There is **no
model-judged quality score**, and `docs/evals.md` says what would have to be true first. Two
eval cases expect `allowed` on purpose, to record where the deterministic injection filter and
the redaction list end. Throughput and latency under load are still not measured. Every
organisation, person and record is invented.

**Shots:**
01-llmops-per-provider.png — the new `/llmops` page with the per-provider table boxed: calls,
p50/p95, tokens, estimated cost with its price-table date, guardrail trips and error rate.
02-llmops-span-tree.png — the real span tree of one governed run rendered in the dashboard,
`atmpl.run → atmpl.stage → guardrail, llm.call, guardrail`, from this process's own spans.
03-evals-report.png — 61 cases across six gated categories, with the gated pass rate CI
refuses to ship below.
04-evals-allowed-cases.png — the case table, highlighting that cases expecting `allowed` are
part of the suite: a gate that refuses everything would otherwise score 100 %.
05-metrics.png — the Prometheus exposition: model calls by provider and outcome, guardrail
decisions by phase, webhook deliveries.
06-readonly-demo.png — the hosted demo's banner: every page readable, every mutation refused,
no key spendable from a public URL.

**LinkedIn paste:**
v0.3.0 of my governed AI automation platform: every model call is now traced, costed and
scored. OpenTelemetry gives a run a real span tree (run → stage → guardrail | llm.call), a new
LLMOps page shows p50/p95, tokens and estimated cost per provider, and one router puts Gemini,
Anthropic and any OpenAI-compatible endpoint behind a single interface with retries, a fallback
chain and a circuit breaker. The red-team suite became 61 scored eval cases that gate CI.
Three things I only learned by calling the real API: two Gemini models I had picked are gone
for new users; a thinking model bills its thoughts, so counting only answer tokens
under-reported cost thirtyfold; and a free-form map is expressible in JSON Schema but not in
any provider's structured-output mode — it comes back empty, every time. All three are in the
ADRs, because the interesting part of building on models is what the measurement contradicts.

**Surfaces:** [ ] showcase-pdf [ ] resume [ ] website [ ] linkedin
