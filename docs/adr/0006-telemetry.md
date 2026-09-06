# 0006 — OpenTelemetry traces, Prometheus metrics, and an in-process model-call ledger

Status: accepted (v0.3.0, 2026-09-06)

## Context

v0.2 could prove that a decision was *governed* — the hash-chained ledger says who decided
what, when, and under which guardrail. It could not answer any question an operator asks the
morning after: how slow is this, how much did it cost, which provider answered, and how often
does a guardrail actually fire.

Three different questions, three different lifetimes:

1. *What happened inside one run?* — a causal question, best answered by a trace.
2. *How is the fleet behaving?* — an aggregate question, best answered by counters a
   scrape can read.
3. *What did the models do, in enough detail to put on a page?* — needs per-call rows, but
   not the durability of a governed record.

## Decision

**Traces.** OpenTelemetry with four span names — `atmpl.run`, `atmpl.stage`,
`atmpl.guardrail`, `atmpl.llm.call` — nested in that order, plus the FastAPI, SQLAlchemy and
httpx instrumentations so a request's database and outbound calls join the same trace. The
exporter is a setting: `none` (default), `console`, `otlp`.

`none` means *export nowhere*, not *trace nothing*. An in-memory span processor is installed
whatever the exporter is, so the keyless default still records real spans and `/llmops` can
render them without a collector. That is the difference between an observable default and an
observability feature you have to buy into.

**Metrics.** `prometheus_client` on an explicit registry (not the global default, so a test
can build a second one), served at `/metrics`. HTTP requests are labelled by the matched
route *template*, never the path: a counter labelled with every run id is a memory leak with
a graph on top.

**The model-call ledger.** A bounded, thread-safe ring buffer of `LlmCallRecord`, written by
the provider router. It is deliberately **not** a table. A model call is operational
telemetry; the governed record is the audit ledger, which does survive a restart. `/llmops`
says which of the two it is reading, and an empty process renders "no model calls yet"
rather than a row of zeroes.

## Consequences

- One `Observability` object hangs on `AppContext`; the engine, the router and the handlers
  all read the same instruments. There is no module-level global to get out of step.
- A guardrail verdict is attached back to the call whose output it judged
  (`CallLedger.mark_guardrail`), so "guardrail trips" is a per-provider column rather than a
  separate table nobody joins.
- **Restarting the container empties the model-call table.** Accepted, and stated on the
  page. Persisting it would make an operational convenience look like evidence.
- The span-tree test asserts the *shape* of a run, not a span count. The fail-first proof in
  `docs/proof/telemetry_span_gate.txt` swaps one guardrail span for a stage span — the count
  is unchanged at five, and the test still catches it.

## Alternatives considered

- **OpenTelemetry metrics instead of `prometheus_client`.** Fewer dependencies in theory,
  but the exposition endpoint would then need the OTLP-to-Prometheus bridge to be useful in
  the k8s overlay, which is more moving parts for the same scrape.
- **Persisting model calls to the database.** Rejected: it puts telemetry and evidence in one
  store, and the first person to notice would reasonably assume the call log has the same
  guarantees as the hash chain. It does not.
