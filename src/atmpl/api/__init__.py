"""The versioned machine API. Mounted at `/api/v1`; its own OpenAPI document lives there."""

from __future__ import annotations

from fastapi import FastAPI

from atmpl import __version__
from atmpl.api.errors import install_error_handlers
from atmpl.api.routers import audit, discovery, health, oauth, runs, templates, webhooks

API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

DESCRIPTION = """
Call the governed workflow engine from another system.

**What this API will not do.** It will not approve anything on your behalf: a MEDIUM or
HIGH stage moves only through `POST /runs/{run_id}/stages/{stage_key}/decision`, carrying a
signed human decision, and the authenticated principal is written into the hash-chained
ledger beside it. It will also not let a caller choose the model adapter — that is a
deployment setting (invariant **G7**).

**Authentication.** Send `Authorization: Bearer <credential>`, where the credential is
either a scoped API key (`atmpl_...`, minted with `atmpl keys create`) or a short-lived
access token from `POST /oauth/token`. Scopes are enforced per route and listed in each
route's errors.

**Errors.** Every failure is `{"error": {"code", "message", "details"}}`. Every response
echoes the `X-Request-Id` you sent, or one generated for you.

All demo organizations, people, and records reachable through this API are synthetic.
"""

TAGS_METADATA = [
    {"name": "ops", "description": "Liveness and readiness probes."},
    {"name": "auth", "description": "OAuth2 client-credentials token endpoint."},
    {"name": "templates", "description": "The base templates this deployment can compile."},
    {
        "name": "discovery",
        "description": "Structured intake: ask the typed questions, validate, compile.",
    },
    {"name": "runs", "description": "Governed runs, their stages, and human decisions."},
    {"name": "audit", "description": "The append-only, hash-chained ledger."},
    {
        "name": "webhooks",
        "description": "Inbound events that start runs; signed outbound deliveries with receipts.",
    },
]


def create_api(context) -> FastAPI:
    """Build the `/api/v1` application. Mounted by :func:`atmpl.web.app.create_app`."""
    api = FastAPI(
        title="Enterprise AI Automation Templates API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "Mohamed Mahmoud", "email": "medo433447@gmail.com"},
        license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        servers=[{"url": API_PREFIX, "description": "This deployment"}],
    )
    api.state.context = context
    install_error_handlers(api)
    for module in (health, oauth, templates, discovery, runs, audit, webhooks):
        api.include_router(module.router)
    return api
