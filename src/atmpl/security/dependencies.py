"""FastAPI dependencies that turn a request into a scoped principal, or refuse it."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from atmpl.api.errors import ApiError
from atmpl.runtime import AppContext
from atmpl.security.credentials import authenticate_api_key
from atmpl.security.principals import (
    ALL_SCOPES,
    DEMO_PRINCIPAL,
    Principal,
    PrincipalKind,
    parse_scopes,
)
from atmpl.security.tokens import TokenError, read_access_token, read_session

SESSION_COOKIE = "atmpl_session"


def context_of(request: Request) -> AppContext:
    return request.app.state.context


def session_principal(request: Request) -> Principal | None:
    """A logged-in dashboard human, or the open demo human when no admin is configured."""
    context = context_of(request)
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        try:
            session = read_session(cookie, key=context.session_key)
        except TokenError:
            return None
        return Principal(
            kind=PrincipalKind.HUMAN,
            subject=session.user,
            scopes=ALL_SCOPES,
            credential_id=None,
        )
    if context.settings.demo_mode:
        return DEMO_PRINCIPAL
    return None


def credential_principal(request: Request) -> Principal | None:
    """An ``Authorization: Bearer`` API key or OAuth2 access token."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    context = context_of(request)
    if token.startswith("atmpl_"):
        with context.engine.database.session() as session:
            return authenticate_api_key(session, token)
    try:
        claims = read_access_token(
            token,
            key=context.jwt_key,
            key_id=context.settings.jwt_key_id,
        )
    except TokenError as exc:
        raise ApiError(401, "unauthorized", str(exc)) from exc
    return Principal(
        kind=PrincipalKind.CLIENT,
        subject=str(claims.get("sub", "unknown")),
        scopes=parse_scopes(str(claims.get("scope", ""))),
        credential_id=str(claims.get("sub", "unknown")),
    )


def api_principal(request: Request) -> Principal:
    """Machine API rule: a credential, or a session, or the explicit open-demo switch."""
    principal = credential_principal(request)
    if principal:
        return principal
    context = context_of(request)
    cookie_principal = session_principal(request)
    if cookie_principal and cookie_principal.kind is PrincipalKind.HUMAN:
        return cookie_principal
    if context.settings.demo_open_api and cookie_principal:
        return cookie_principal
    raise ApiError(
        401,
        "unauthorized",
        "Provide an API key or OAuth2 access token in the Authorization header.",
        details={"hint": "POST /api/v1/oauth/token, or run: atmpl keys create --name <client>"},
    )


def dashboard_principal(request: Request) -> Principal:
    """Dashboard rule: a session, the open demo, or an API credential (superset)."""
    principal = session_principal(request)
    if principal:
        return principal
    credential = credential_principal(request)
    if credential:
        return credential
    raise ApiError(401, "unauthorized", "Sign in to the approvals dashboard first.")


def require_scope(scope: str) -> Callable[[Request], Principal]:
    """Route dependency: authenticate, then enforce one scope."""

    def dependency(request: Request) -> Principal:
        principal = api_principal(request)
        if not principal.has(scope):
            raise ApiError(
                403,
                "insufficient_scope",
                f"This credential is missing the '{scope}' scope.",
                details={"required": scope, "granted": sorted(principal.scopes)},
            )
        request.state.principal = principal
        return principal

    return dependency


def require_scope_flexible(scope: str) -> Callable[[Request], Principal]:
    """Like :func:`require_scope` but also accepts a dashboard session (legacy routes)."""

    def dependency(request: Request) -> Principal:
        principal = dashboard_principal(request)
        if not principal.has(scope):
            raise ApiError(
                403,
                "insufficient_scope",
                f"This credential is missing the '{scope}' scope.",
                details={"required": scope, "granted": sorted(principal.scopes)},
            )
        request.state.principal = principal
        return principal

    return dependency
