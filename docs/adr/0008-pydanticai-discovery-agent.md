# 0008 — A PydanticAI discovery agent, held to the template's own model

Status: accepted (v0.3.0, 2026-09-06)

## Context

Discovery is the consulting step this product models: ask an organisation the questions a
governed workflow needs answered, validate the answers, compile. The questions were already
typed (`atmpl.models`) and the validation already fail-closed. What was missing is the part a
consultant does in the room — reading a paragraph about how a process actually runs and
turning it into those answers.

An agent is a good fit and a real risk. The good fit: this is extraction into a known schema
with a tool loop over a catalog. The risk: an agent that answers something *near* the
questionnaire, and a compiler that merges it.

## Decision

A PydanticAI `Agent` whose result type is built from the template's own placeholder model,
with three read-only tools (`list_required_fields`, `get_template`, `search_catalog`), and
whose output is then re-validated by `validate_answers` — the same function the CLI and the
API use. A reason and a `stated | inferred` confidence per field is required, not optional.

**The agent runs on the deployment's provider router**, through a PydanticAI `FunctionModel`
whose function is one `router.complete()` call. PydanticAI is worth having for the typed
result, the tool loop and the retry-on-wrong-shape; it is not worth having a second path to
a provider, because half the model calls would then miss the span, the cost estimate and the
LLMOps page.

**Two boundaries are enforced, not requested in a prompt:**

1. A field the template does not declare is a refusal (`AgentRefused`, HTTP 422 naming the
   field), never a merge. Fail-first proof: `docs/proof/agent_invented_field_gate.txt`.
2. Nothing is created. `POST /api/v1/discovery/agent` returns a draft and, optionally, opens
   a discovery session for a human to edit. No organisation, workflow or run is created and
   no decision is made — a human still calls `resolve` (G1, G2).

## The one thing the schema could not express

`approval_authority_matrix` is a `dict[str, str]`. That is expressible in JSON Schema and
**not** expressible in any provider's structured-output mode: measured on `gemini-3.6-flash`,
an object with no declared properties comes back as `{}` every time, because the model is
given no slot to fill. The first live run produced exactly that, plus `locales` as a bare
string, and the questionnaire correctly refused to compile.

So authority maps travel over the wire as `{key, value}` rows and are rebuilt into a
dictionary before the real model validates. The agent's result type is therefore the
placeholder model *reshaped*, not a second contract: same fields, same names, same enums,
with only free-form maps changed. Eval case `E-AGENT-09` asserts that an empty map still
fails, because a workflow nobody is authorised to approve must not compile.

## Consequences

- The API route is **synchronous**. `Agent.run_sync` starts its own event loop, and calling
  it from an `async def` handler raises *"This event loop is already running"*; a sync
  handler goes to FastAPI's threadpool, which is where a call that can take half a minute
  belongs anyway.
- On the mock provider the agent is deterministic and makes exactly two calls — one tool
  call, one answer — so CI exercises the loop keyless.
- Live on `gemini-3.6-flash`, one retail description produced a valid questionnaire in
  **three attempts across 58 s** (one 5xx retried), with two open questions the model
  correctly declined to guess. Both are the intended behaviour, and both are in the brief.
- `pydantic-ai-slim` is a runtime dependency. ATMPL's promise is *keyless*, not
  *dependency-free* (that is RelayOps and PetPoint), so this is inside the repository's
  existing contract.

## Alternatives considered

- **LangGraph.** More graph than this needs: discovery is one typed extraction with a tool
  loop, not a state machine with interrupts.
- **A hand-rolled loop.** It would have been perhaps eighty lines, and would have re-invented
  the retry-on-validation-failure that is the single most valuable thing PydanticAI does here.
- **Making the placeholder model the output type unchanged.** Tried first; it is what
  produced `{}` for the authority matrix. Recorded above rather than quietly worked around.
