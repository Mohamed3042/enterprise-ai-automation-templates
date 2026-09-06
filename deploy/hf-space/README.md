---
title: ATMPL — Governed AI Automation
emoji: 🔒
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: A read-only demo of governed, human-authority AI automation
---

# Enterprise AI Automation Templates — public demo

> **Not the live demo.** As of 2026-09-06, Hugging Face returns
> `402 Payment Required` when creating a Docker Space: *"Static Spaces are free for everyone,
> but hosting Gradio and Docker Spaces on free cpu-basic requires a PRO subscription."*
> Measured — `hf auth login` succeeded and `create_repo` still refused. The live demo is on
> Render (`deploy/render/`); this folder is kept because it costs nothing and works the day
> the account is PRO.

A **read-only** deployment of
[Mohamed3042/enterprise-ai-automation-templates](https://github.com/Mohamed3042/enterprise-ai-automation-templates).

## What you are looking at

Three synthetic organisations — a retail chain, a ministry of education and a regional bank —
each running a governed workflow compiled from a typed discovery questionnaire. The product's
claim is a boundary: **AI drafts, a named human decides.** A MEDIUM or HIGH stage moves only
on a signed human decision, and every transition lands in a SHA-256 hash-chained ledger.

| Page | What it shows |
| --- | --- |
| `/` | The three demos, their workflows and the open human gates |
| `/approvals` | The queue a reviewer actually works |
| `/audit` | The hash-chained ledger, and its verification |
| `/evals` | The scored eval suite: R1–R8 plus the prompt-injection, PII, authority and typed-output cases |
| `/llmops` | Every model call this container has made: latency, tokens, estimated cost, guardrail trips |
| `/api/v1/docs` | The OpenAPI document for the machine API |
| `/metrics` | Prometheus exposition |

## The boundary, stated plainly

- **Every organisation, person, record and event here is synthetic.** Nothing describes a real
  client, and no data from any employer appears in this repository.
- **This Space is read-only.** Every non-GET request is refused with `demo_readonly`, so nobody
  can approve, reject, start a run or spend a model key from here. To make a decision, run it
  locally — one command, no key:

  ```bash
  docker run -p 8000:8000 ghcr.io/mohamed3042/enterprise-ai-automation-templates:latest
  ```

- **The provider is the deterministic offline mock**, so the numbers on `/llmops` are real
  calls with real latencies and real token counts — of a local function, not of a hosted model.
  The Gemini adapter is live-verified in CI against `gemini-3.6-flash`; the Anthropic and
  OpenAI-compatible adapters are contract-tested against recorded responses. This Space uses a
  hosted provider only if the Space owner adds a key as a Space secret, which is deliberately
  not the default.
- **Costs shown are estimates** from a committed price table with a date on it, never billing
  figures. A model that table does not know is shown as *not priced*, never as zero.

Built by **Mohamed Mahmoud** · [source](https://github.com/Mohamed3042/enterprise-ai-automation-templates)
