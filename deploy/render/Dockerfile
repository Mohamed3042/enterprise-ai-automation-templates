# The public demo, built by the host from this repository.
#
# Render's free plan builds a Dockerfile from a connected Git repo. Deploying a *prebuilt*
# registry image and applying a Blueprint both asked for a card (measured 2026-09-06), so this
# file takes the plainly-free path and keeps the property that mattered: the base is the exact
# image CI published, so the running bytes are still the ones that were tested, and the only
# thing built here is three lines of configuration on top.
#
# The tag is pinned. `:latest` would mean the demo changes under its own link the day another
# branch merges; a new release is a one-line edit here.
FROM ghcr.io/mohamed3042/enterprise-ai-automation-templates:v0.3.0

# Everything the demo needs, so the dashboard form needs no environment variables at all.
ENV ATMPL_DEMO_READONLY=1 \
    ATMPL_ADAPTER=mock \
    ATMPL_DATABASE_URL=sqlite:///var/atmpl.db \
    ATMPL_AUTO_CREATE_SCHEMA=true \
    ATMPL_RATE_LIMIT=60/minute \
    ATMPL_METRICS_ENABLED=1 \
    ATMPL_METRICS_PUBLIC=1 \
    ATMPL_JSON_LOGS=1 \
    ATMPL_TRACE_EXPORTER=none \
    ATMPL_WEBHOOK_WORKER=0

# The port is passed explicitly in the CMD rather than relied on from the base image: the
# pinned v0.3.0 image predates `demo up` reading $PORT, and a demo that silently binds the
# wrong port is a service the host marks unhealthy for no visible reason. Measured — it bound
# 8000 and the health check never answered.
ENV PORT=10000
EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import os,sys,urllib.request; p=os.getenv('PORT','10000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=4).status == 200 else 1)"

# Two things happen before the server starts, and both are the deployment measuring itself:
# `evals run` scores the suite in this container, so /evals shows this deployment's own
# report; and `--exercise` drafts one AI stage per demo, so /llmops shows real calls with real
# latencies instead of correctly reporting an idle process.
CMD ["sh", "-c", "python -m atmpl evals run --out var/evals; exec python -u -m atmpl demo up --host 0.0.0.0 --port ${PORT:-10000} --no-open-api --exercise"]
