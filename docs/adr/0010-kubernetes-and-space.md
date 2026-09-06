# 0010 — Kubernetes manifests, and a read-only public demo

Status: accepted (v0.3.0, 2026-09-06)

## Context

v0.2 published an image to GHCR and a Compose file. Neither answers the two questions that
follow: *how would this run in a cluster*, and *where can I look at it without installing
anything*.

## Decision — Kubernetes

`deploy/k8s/base` plus kustomize overlays `dev` and `prod`. Base carries the Deployment
(three probes, resource requests **and** limits, non-root uid 10001 matching the Dockerfile,
`readOnlyRootFilesystem`, all capabilities dropped), a Service, a ConfigMap mirroring
`.env.example`, a Secret **template** whose every value is a visible `REPLACE_…` placeholder,
an HPA, an Ingress and a default-deny NetworkPolicy with explicit allows.

Two details are decisions rather than boilerplate:

- **A read-only root filesystem needs somewhere to write.** The hash-chained ledger is a
  file. Without the `/app/var` mount the pod starts and cannot audit — which is the same
  failure the Compose file hit in v0.2, in a new place.
- **`prod` migrates in an init container.** Two replicas must not race to create the same
  schema, so `atmpl db upgrade` runs before either serves and `ATMPL_AUTO_CREATE_SCHEMA` is
  false. `dev` is the opposite on purpose: SQLite on an `emptyDir`, one replica, nothing to
  provision — which is exactly what makes it the overlay CI can stand up on kind.

CI runs `kubeconform -strict` over both rendered overlays, then builds the image, loads it
into a kind cluster, applies `dev`, waits for the rollout and curls `/ready`, `/metrics` and
`/llmops` through a port-forward. `tests/test_deploy.py` additionally asserts the *promises*
— non-root, read-only, three probes, default-deny, placeholders-only — without needing a
cluster, and checks every ConfigMap key against `Settings.model_fields`, because
pydantic-settings ignores a key it does not recognise and a typo would otherwise be silent.

## Decision — the public demo

A container host that runs the published image with a handful of environment variables and no
rebuild, so the public URL is byte-for-byte the image on GHCR.

**The host changed once, and the reason is worth keeping.** The plan was a Hugging Face Docker
Space. `hf auth login` succeeded as `Medo4334`, and `HfApi.create_repo` then returned
`402 Payment Required`: *"Static Spaces are free for everyone, but hosting Gradio and Docker
Spaces on free cpu-basic requires a PRO subscription."* That is a pricing change, not a
mistake in the code — so `deploy/hf-space/` is kept intact for the day the account is PRO, and
the live demo moved to **Render** (`render.yaml` at the repository root, because that is the
only place Render looks for a Blueprint).

The trade is stated rather than hidden: Render's free instance spins down after ~15 minutes
idle, so the first request after that waits ~30 s. `deploy/render/README.md` says so, because
a reviewer who thinks a service is broken is worse off than one who was told it sleeps.

`ATMPL_DEMO_READONLY=1` refuses every non-GET request in middleware, with a banner saying so.
That is the whole safety model of a public demo: every page can be read, nothing can be
changed, no key can be spent, and the invitation is to run it locally where a decision means
something.

Two things happen before the server starts, and both are the deployment measuring itself:
`atmpl evals run` scores the suite *in that container*, so `/evals` shows the deployment's own
report; and `demo up --exercise` drafts one AI stage per demo, so `/llmops` shows real calls
with real latencies rather than correctly reporting an idle process. Seeding numbers would
have been easier and would have made the page a picture of a dashboard.

The Space runs the **mock** provider. A hosted provider is enabled only if the Space owner
adds a key as a Space secret, deliberately not by default: a public URL with a live key is a
bill waiting for a crawler.

## Consequences

- The Space Dockerfile pins `:v0.3.0`, not `:latest`. A demo that changes under its own link
  the day another branch merges is not a demo of anything.
- `kind` and `kubeconform` are not installed on the build machine; the manifests are rendered
  and reviewed locally with `kubectl kustomize`, and both tools run in CI. That split is
  stated rather than papered over.
- Publishing needs one browser step on the host's own dashboard, and no credential ever
  leaves it: Render reads `render.yaml` from the connected repository. CI then redeploys
  through a deploy hook when `RENDER_DEPLOY_HOOK` exists, and measures the URL when a
  `DEMO_URL` repository variable exists — waiting for a cold start rather than asserting on
  one request.
- `demo up --port` now defaults to `$PORT`, which is how Render, Cloud Run and Fly all tell a
  service where to listen. Nothing changes locally: without `PORT`, it is still 8000.

## Alternatives considered

- **Helm.** A chart is the right answer for something other people install. This is one
  Deployment with two overlays; kustomize renders it with no templating language in between,
  and `kubectl kustomize` is in every kubectl.
- **Google Cloud Run**, which would make the `cloud` claim name-exact and is the gaps plan's
  own alternative. Rejected for now on one property: its free tier still requires billing
  enabled, and a card on a portfolio demo is a standing risk for no functional gain. Render's
  free plan needs none. If the CV line matters more than the card later, `render.yaml` is
  twenty lines and the image is the same.
