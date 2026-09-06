# 0007 — One provider router, and what happened to `atmpl.adapters`

Status: accepted (v0.3.0, 2026-09-06). Supersedes the adapter selection in
[0001](0001-api-versioning.md)'s era of the codebase.

## Context

v0.2 had two adapters: a deterministic mock and a Claude adapter that had never been
selected in anger. `create_adapter()` picked one from an environment variable and returned
it. Everything a real deployment needs around a model call — a timeout, a retry, a second
provider when the first is down, a cost estimate, a span — did not exist, and adding each of
them to each adapter would have meant writing them three times and forgetting them the
fourth.

## Decision

A `Provider` protocol so small that writing a new one is uninteresting: messages in;
optional tools and an optional JSON schema; text, JSON, tool calls, usage and latency out.
Four implementations — `mock`, `gemini`, `anthropic`, `openai_compat` — and one
`ProviderRouter` that owns everything else:

- the chain (`ATMPL_ADAPTER` first, then `ATMPL_PROVIDER_FALLBACKS`),
- bounded retries with exponential backoff, only for error kinds that are retryable,
- a circuit breaker that skips a provider after N consecutive failures until a cooldown,
- the span, the counters, the ledger row and the cost estimate.

`atmpl.adapters` remains importable and remains **fail-closed**: naming a provider whose key
is absent still raises rather than silently falling back to the mock. What changed is what it
returns — a `RouterAdapter` over the router — so v0.1 code inherits the retries and the
telemetry it never asked for.

**A caller never chooses a provider.** The chain is a settings value; there is no request
field for it (invariant G7). An organisation profile could reasonably select one in a later
version; today it does not, and this ADR is where that limitation is recorded rather than
implied.

## Consequences

- The mock reproduces the v0.1 content byte for byte, so the seeded demos' audit hashes did
  not move. `tests/test_adapters.py` asserts that, not just that a mock exists.
- **Gemini is live-verified; Anthropic and OpenAI-compatible are contract-tested.** No key
  for the latter two exists on the machine this was built on, so their request shape,
  response parsing and error mapping are pinned to recorded bodies in `tests/cassettes/`,
  authored from the vendors' documented schemas. `tests/cassettes/README.md` says so, the
  README says so, and neither says "supports all major providers".
- Two things were measured rather than assumed, and both changed the code:
  - `gemini-2.0-flash` and `gemini-2.5-flash` are **gone** for new API users — the API says
    so in the 400 it returns. The default is `gemini-3.6-flash`, which is what it recommends.
  - A thinking model bills its thoughts. A live answer of **61 tokens carried 1,844
    reasoning tokens**; counting only `candidatesTokenCount` under-reports the cost by thirty
    times, so `_usage()` adds `thoughtsTokenCount` to the output count.
- Cost is an **estimate** from `providers/prices.yaml`, which carries the date it was read
  and the date it stops being right (Google publishes a step change on 2027-01-01). A model
  the table does not know is reported as *not priced*, never as zero — the fail-first proof
  for that is `docs/proof/cost_honesty_gate.txt`.

## Alternatives considered

- **A vendor SDK per provider.** Three more dependency trees, three more upgrade paths, and
  the wire shapes are four fields each. `httpx` was already a dependency.
- **LiteLLM or a similar aggregator.** It would have supplied the chain and the cost table,
  and removed the thing worth showing: that this repository knows what a 429 means, what a
  reasoning token costs, and what its own circuit breaker does.
