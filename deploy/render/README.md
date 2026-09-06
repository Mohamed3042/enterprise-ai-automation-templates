# The public demo, on Render

`deploy/render/Dockerfile` is `FROM` the exact image CI published, plus the environment a
public demo needs and nothing else. Render builds it; the running bytes are still the ones
that were tested.

## Publishing it (browser, once — no card, no Blueprint)

1. <https://dashboard.render.com> → sign in with GitHub.
2. **New +** → **Web Service**.
3. **Build and deploy from a Git repository** → `Mohamed3042/enterprise-ai-automation-templates`,
   branch `main`.
4. Set exactly three fields, then **Deploy**:

   | Field | Value |
   |---|---|
   | Language / Runtime | **Docker** |
   | Dockerfile Path | `./deploy/render/Dockerfile` |
   | Instance Type | **Free** |

   Leave **Environment Variables** empty. Everything the demo needs is in the Dockerfile, so
   there is no form field to mistype into a deployment that quietly is not read-only.

The first build pulls a 325 MB base image, so give it a few minutes.

### Why not the Blueprint

`render.yaml` is committed and correct, and **Apply Blueprint asked for a card** on
2026-09-06 — as did deploying a prebuilt registry image (`runtime: image`). The path above
avoids both. The Blueprint is kept for whenever a paid plan makes it available; it points at
the same Dockerfile, so the two cannot drift.

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
Render rebuilds on push to `main`. To let CI force it, add the hook from
**Settings → Deploy Hook**:

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
