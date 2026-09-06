# The public demo, on Render

The Blueprint is [`render.yaml`](../../render.yaml) at the repository root — Render only looks
for it there. It runs the image CI published, so the public URL is the same artifact as
`docker run ghcr.io/mohamed3042/enterprise-ai-automation-templates:v0.3.0`; the only difference
is the environment.

## Publishing it (browser, once)

1. <https://dashboard.render.com> → sign in with GitHub.
2. **New** → **Blueprint**.
3. Pick `Mohamed3042/enterprise-ai-automation-templates`, branch `main`.
4. Render reads `render.yaml`, shows one web service named `atmpl-governed-automation` on the
   **free** plan → **Apply**.

No credential is pasted anywhere outside Render's own login. The first deploy pulls a 325 MB
image, so give it a few minutes.

## What to expect

| | |
|---|---|
| URL | `https://atmpl-governed-automation.onrender.com` (Render assigns it; confirm in the dashboard) |
| Health check | `/health`, which answers without touching the database |
| Cold start | **~30 s.** A free service spins down after ~15 minutes idle, and the first request after that waits for the container. That is the cost of the free plan and it is worth saying out loud to anyone you send the link to. |
| Storage | Ephemeral. SQLite lives in the container's filesystem and resets on every deploy and every spin-up — which is correct here: the demos are seeded and the eval suite is scored at start-up, so a fresh container is a *freshly measured* one. |
| Provider | The deterministic offline mock. A live key on a public URL is a bill waiting for a crawler. |

## Why the demo is read-only

`ATMPL_DEMO_READONLY=1` refuses every non-GET request in middleware, with a banner saying so:

```json
{"error": {"code": "demo_readonly", "message": "This is a read-only public demo. Run it locally to make decisions: …"}}
```

Every page can be read; nothing can be changed; no key can be spent. To make a decision, run it
locally — one command, no key:

```bash
docker run -p 8000:8000 ghcr.io/mohamed3042/enterprise-ai-automation-templates:latest
```

## Redeploying after a release

The Blueprint pins an image tag, so a new release is two steps: bump `image.url` in
`render.yaml` and push (Render redeploys on the Blueprint change), or hit a deploy hook.

To let CI do it, add the hook from **Settings → Deploy Hook** as a repository secret:

```bash
gh secret set RENDER_DEPLOY_HOOK -R Mohamed3042/enterprise-ai-automation-templates
```

The `render` job in `.github/workflows/ci.yml` calls it on a `main` push when that secret
exists, and does nothing when it does not.

## Why not a Hugging Face Space

`deploy/hf-space/` is complete and still works — but as of **2026-09-06**, creating one returns:

```
402 Client Error: Payment Required
Static Spaces are free for everyone, but hosting Gradio and Docker Spaces
on free cpu-basic requires a PRO subscription.
```

Measured, not assumed: `hf auth login` succeeded as `Medo4334` and `HfApi.create_repo` still
refused. The folder is kept because it costs nothing and works the day the account is PRO.
