# The public demo, on Render

`deploy/render/Dockerfile` is `FROM` the exact image CI published, plus the environment a
public demo needs and nothing else. Render builds it; the running bytes are still the ones
that were tested.

## Publishing it

Render requires payment information on file before it creates **any** service, the Free plan
included — measured 2026-09-06 as `402 Payment information is required to complete this
request` from `POST /v1/services`, and the dashboard's Blueprint and registry-image paths asked
for a card the same day. With a card on file the Free instance costs nothing.

Once: <https://dashboard.render.com/billing> → add a card; **Account Settings → API Keys** →
create a key → `RENDER_API_KEY` in your environment (never in the repository). Then:

```bash
python deploy/render/create_service.py          # create (or reuse), wait until live, measure /health
python deploy/render/create_service.py --check  # measure only
```

The script creates `atmpl-governed-automation` as a Docker web service in Frankfurt on the
Free plan from this repository's `main`, with `/health` as the health check and no environment
variables — everything the demo needs is in the Dockerfile, so there is no form field to
mistype into a deployment that quietly is not read-only. The dashboard form works too
(**New + → Web Service → this repository → Language Docker → Dockerfile Path
`./deploy/render/Dockerfile` → Instance Type Free**).

The first build pulls a 325 MB base image, so give it a few minutes. Live since 2026-09-06 at
<https://atmpl-governed-automation.onrender.com>.

### Why not the Blueprint

`render.yaml` is committed and correct; it points at the same Dockerfile, so the two cannot
drift. Applying it asked for a card before the API path was measured — with a card on file it
is simply the second way to create the same service.

## What to expect

| | |
|---|---|
| Health check | `/health`, which answers without touching the database |
| Cold start | **~30 s.** A free instance spins down after ~15 minutes idle. Worth saying to anyone you send the link to — a reviewer who thinks it is broken is worse off than one who was told it sleeps. |
| Storage | Ephemeral, and correct here: a fresh container re-scores its own eval suite and re-exercises the engine at boot, so what `/evals` and `/llmops` show was measured minutes ago. |
| Provider | The deterministic offline mock. A live key on a public URL is a bill waiting for a crawler. |
| Port | Passed explicitly as `--port ${PORT:-10000}`. The pinned base image predates `demo up` reading `$PORT`, and it bound 8000 and went unhealthy until this was made explicit. |

## Why the demo is read-only

`ATMPL_DEMO_READONLY=1` refuses every non-GET request in middleware, with a banner saying so:

```json
{"error": {"code": "demo_readonly", "message": "This is a read-only public demo. Run it locally to make decisions: …"}}
```

Every page can be read; nothing can be changed; no key can be spent. To make a decision, run
it locally — one command, no key:

```bash
docker run -p 8000:8000 ghcr.io/mohamed3042/enterprise-ai-automation-templates:latest
```

## Redeploying after a release

The Dockerfile pins a base tag, so a new release is a one-line edit to its `FROM` plus a push;
Render rebuilds on push to `main` when the repository is connected, and
`python deploy/render/create_service.py --redeploy` forces a deploy through the API from
anywhere the key is set. To let CI force it, add the hook from **Settings → Deploy Hook**:

```bash
gh secret set RENDER_DEPLOY_HOOK -R Mohamed3042/enterprise-ai-automation-templates
gh variable set DEMO_URL -R Mohamed3042/enterprise-ai-automation-templates --body "https://<service>.onrender.com"
```

The `demo` job in `.github/workflows/ci.yml` then triggers the deploy and polls `DEMO_URL/health`
for up to five minutes — waiting for a cold start rather than asserting on one request. With
neither set, the job prints what to do and exits 0.

## Why not a Hugging Face Space

`deploy/hf-space/` is complete and still works — but as of **2026-09-06**, creating one returns:

```
402 Client Error: Payment Required
Static Spaces are free for everyone, but hosting Gradio and Docker Spaces
on free cpu-basic requires a PRO subscription.
```

Measured, not assumed: `hf auth login` succeeded as `Medo4334` and `HfApi.create_repo` still
refused. The folder is kept because it costs nothing and works the day the account is PRO.
