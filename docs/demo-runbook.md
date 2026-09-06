# Demo runbook — five minutes

What to open, what to click, what the audience sees, and the sentence that keeps it honest.

**Say this once, at the start:** *"Every organization, person and record here is invented. What
is real is the machinery: the rules, the ledger, and the fact that the AI cannot be the one who
decides."*

**A read-only public demo is prepared but not yet published** — `deploy/hf-space/` is
complete and CI-wired, and one interactive `hf auth login` turns it on. Until then, run it
locally; the two steps that change state (§4 and §6) need a local copy anyway.

## Before the room

```bash
docker compose --profile receiver up -d       # api + postgres + a receiver, ~40s the first time
```

or, with nothing but Python 3.12:

```bash
python -m pip install -e ".[dev]" && python -m atmpl demo up
```

Open `http://127.0.0.1:8000`. Keep a second tab on `http://127.0.0.1:8000/api/v1/docs`.
If you are demoing the webhook, copy the `WEBHOOK IN:` secret the command printed.

## 1. The claim (30 seconds) — `/`

Point at the card on the right: **HIGH risk is human-decided**, and the function signature under
it. Say: there is no `auto_approve`, no flag, no second branch. A test asserts those tokens do
not exist anywhere in the runtime.

Point at the yellow banner: this instance is in **demo mode**, so decisions are recorded as
`human:demo`. Setting two environment variables turns on a real login. That boundary is the
honest part of the demo, so name it early rather than hoping nobody asks.

## 2. One template, three regions (45 seconds) — `/`

Open **OmniMart Global**. Two runs carry the *identical* refund facts —
`SYN-REFUND-1042`, 125, day 12. Open each: the EU run routes to a privacy review, the US run
goes straight to a manager. Same template, different compiled policy pack. That is the product:
discovery answers compile into a workflow, not a prompt.

## 3. The AI's place (60 seconds) — `/runs/run_bank_override`

The green panel: an AI draft recommending `ROUTE_TO_TIER_2`, and beside it a human terminal
decision of **reject**. Say: the model summarized and recommended; a named officer decided the
opposite, and the ledger holds both. Scroll to a HIGH stage and read the red line:
*human-only transition · signed decision required*.

## 4. Decide something live (60 seconds) — `/approvals`

Pick the EU regional review. Type a reason, click **Approve**. The result appears in place.
Then open the run: the signed decision panel now names the actor, the role, the signature — and
**Authenticated as**, which is the identity the session actually carried, not just what was
typed. That is the line that turns "we log approvals" into "we can prove who approved".

## 5. The evidence holds (40 seconds) — `/audit`

Click **Verify chain**. Every JSONL record embeds the previous hash; verification recomputes
the whole chain and reports the count. Say: change one historical byte and this goes red — case
R5 does exactly that.

## 5b. And the claim is scored (40 seconds) — `/evals`

The 24 red-team contracts are still here, and they are now 24 of **61 scored cases** with an
expected decision recorded beside each one. Point at two things:

- the **gated pass rate**, and that `atmpl evals gate --min-pass 1.0` is a CI step, so a
  regression fails the build rather than changing a page;
- the cases whose expected decision is **allowed**. Say: a filter that refuses everything
  passes every attack, so the suite measures the false-positive side too — and two of those
  cases exist to record where the deterministic filter *ends*, which is in `docs/evals.md`
  rather than in a footnote nobody reads.

## 5c. Every model call, measured (45 seconds) — `/llmops`

Per provider and model: calls, p50/p95, tokens in and out, estimated cost, guardrail trips,
error rate. Then the span tree of one real run — `atmpl.run → atmpl.stage → guardrail,
llm.call, guardrail` — and the call log underneath.

Three sentences worth saying here:

1. **Nothing on this page is sampled or seeded.** A fresh process shows "no model calls yet"
   and means it; these rows are calls this container made.
2. **Cost is an estimate**, from a committed price table with the date it was read on it. A
   model the table does not know reads *not priced* — never `$0.00`.
3. **The provider is chosen by the deployment, never by the caller.** That is invariant G7,
   and the providers table at the bottom of the page is the whole configured chain.

If a collector is running (`docker compose --profile tracing up`), the same trace is in Jaeger
at `http://127.0.0.1:16686` — service `atmpl`, operation `atmpl.run`.

## 6. It has an outside (60 seconds) — `/webhooks`

Two directions, and both are on this page.

**Inbound** — paste into a terminal (the secret was printed at startup):

```bash
SECRET=<the WEBHOOK IN secret>
BODY='{"ticket":{"id":"SYN-TCK-8801","refund_amount":125,"subject":"Returned on day 12"}}'
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -r | cut -d' ' -f1)
curl -X POST http://127.0.0.1:8000/api/v1/webhooks/helpdesk \
  -H "content-type: application/json" -H "X-ATMPL-Signature: t=$TS,v1=$SIG" \
  -H "X-Idempotency-Key: SYN-TCK-8801" -d "$BODY"
```

Refresh the dashboard: a new governed run, titled from the ticket, with the event recorded as
evidence on its first stage — and the customer email already `[REDACTED]`, because inbound
payloads go through the same deterministic policy a model call does. Run the command again: the
response says `idempotent_replay: true` and no second run appears.

**Outbound** — the decision from step 4 produced deliveries on `/webhooks`: event type, status
code, latency, attempts. `docker compose logs receiver` shows the receiver verifying our
signature on the other side. If a receiver fails, the row retries with backoff and ends in
`dead_letter` with a **Retry delivery** button.

## 6b. It can do the discovery interview (45 seconds) — terminal

```bash
python -m atmpl discover --text "A European retail chain handles about 4,000 refund requests \
a month. A store manager approves refunds up to 250 EUR; the regional director approves \
anything above and owns escalations. Order records are confidential."
```

Out comes the template's typed questionnaire, filled in, with a reason and a
`stated | inferred` confidence per field — plus the open questions it declined to guess.

Then say the two things that make it a *governed* agent rather than a demo of an agent:

- it answers **only** the fields the template declares; an invented field is refused with a
  422 naming it, not merged;
- it decides nothing. The output is a draft a human edits and then compiles with `resolve`.

`POST /api/v1/discovery/agent` is the same thing over HTTP. On the mock provider it is
deterministic; live on `gemini-3.6-flash` it took three attempts across 58 seconds, one of
them a retried 5xx — which is on `/llmops` too.

## 7. It is an API, not a screen (30 seconds) — `/api/v1/docs`

Every screen you just used is a route here: templates, discovery sessions, runs, decisions,
audit, webhooks. Scroll the description at the top: *"It will not approve anything on your
behalf."* Mention `atmpl keys create --scopes` and the OAuth2 token endpoint, and that the
schema in front of them is compared against a committed snapshot in CI, so it cannot drift.

## If someone asks

| Question | Answer |
|---|---|
| "Is this using a real model?" | Not by default. The mock provider is deterministic and offline. Gemini is live-verified in CI; the Anthropic and OpenAI-compatible adapters are contract-tested against recorded responses, because no key for either exists on the build machine. |
| "How much does a run cost?" | `/llmops` shows an estimate from a committed price table with a date on it. It is not a billing figure, and a model the table does not know reads *not priced*. |
| "What happens when the provider is down?" | Retries with backoff on retryable errors, then the next provider in the configured chain, then a circuit breaker that stops trying for a cooldown. Every attempt is a row on `/llmops`. |
| "Does the agent decide anything?" | No. It drafts a questionnaire, with a reason per field, and a human compiles it. It cannot even add a field the template does not declare. |
| "Could someone bypass the human?" | Not through this code. There is one HIGH transition and it takes a signed decision; R2 in the red team asserts no other input path exists. |
| "Is the data real?" | No. Every organization, person and record is invented, and every page says so. |
| "Is it compliant?" | No claim is made. It demonstrates the engineering controls a compliance programme would map policy onto. |
| "What is not protected?" | Demo mode, ephemeral signing keys, recoverable webhook secrets, single-process rate limiting, and synthetic signatures — all listed in SECURITY.md. |

## Tear down

```bash
docker compose --profile receiver --profile tracing down -v
```
