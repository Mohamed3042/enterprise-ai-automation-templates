# syntax=docker/dockerfile:1

# --- builder: resolve and build a wheel, so the runtime carries no build toolchain -------
FROM python:3.12-slim-bookworm AS builder

WORKDIR /src
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --upgrade "pip==25.3" "build==1.3.0" \
 && python -m build --wheel --outdir /dist

# --- runtime: slim, non-root, healthchecked ----------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="Enterprise AI Automation Templates" \
      org.opencontainers.image.description="Governed, discovery-led enterprise AI automation templates" \
      org.opencontainers.image.source="https://github.com/Mohamed3042/enterprise-ai-automation-templates" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    ATMPL_PROJECT_ROOT=/app

WORKDIR /app

COPY --from=builder /dist/*.whl /tmp/
RUN python -m pip install /tmp/*.whl "psycopg[binary]==3.2.12" \
 && rm -rf /tmp/*.whl

# Template artifacts and demo answers are data, not code: they live beside the app.
COPY templates ./templates
COPY demos ./demos
COPY alembic.ini ./alembic.ini

RUN useradd --create-home --uid 10001 atmpl \
 && mkdir -p /app/var \
 && chown -R atmpl:atmpl /app
USER atmpl

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "atmpl", "demo", "up", "--host", "0.0.0.0", "--port", "8000"]
