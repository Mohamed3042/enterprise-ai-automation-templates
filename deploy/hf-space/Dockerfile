# The Space runs the same image CI publishes — it does not rebuild the application. What a
# reviewer sees at the public URL is therefore byte-for-byte the image on GHCR, and the only
# difference between the two is the environment below.
#
# The tag is pinned. `:latest` would mean the Space silently changes under the link in the
# README the day another branch merges.
FROM ghcr.io/mohamed3042/enterprise-ai-automation-templates:v0.3.0

# Hugging Face routes to one port; 7860 is the default it expects.
ENV ATMPL_DEMO_READONLY=1 \
    ATMPL_DEMO_OPEN_API=0 \
    ATMPL_ADAPTER=mock \
    ATMPL_DATABASE_URL=sqlite:///var/atmpl.db \
    ATMPL_AUTO_CREATE_SCHEMA=true \
    ATMPL_RATE_LIMIT=60/minute \
    ATMPL_METRICS_ENABLED=1 \
    ATMPL_METRICS_PUBLIC=1 \
    ATMPL_JSON_LOGS=1 \
    ATMPL_TRACE_EXPORTER=none \
    ATMPL_WEBHOOK_WORKER=0 \
    ATMPL_PUBLIC_BASE_URL=https://medo4334-atmpl-governed-automation.hf.space

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/health', timeout=4).status == 200 else 1)"

# `--exercise` drafts one AI stage per demo at start-up, so /llmops shows real calls with
# real latencies instead of correctly reporting an idle process. `--no-open-api` keeps the
# machine API credential-only even though the dashboard is open.
CMD ["python", "-m", "atmpl", "demo", "up", \
     "--host", "0.0.0.0", "--port", "7860", \
     "--no-open-api", "--exercise"]
