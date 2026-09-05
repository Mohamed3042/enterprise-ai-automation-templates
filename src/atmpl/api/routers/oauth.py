"""OAuth2 client-credentials: a client id and secret in, a short-lived scoped JWT out."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request

from atmpl.api.errors import ApiError
from atmpl.api.schemas import TokenResponse
from atmpl.security.credentials import authenticate_oauth_client
from atmpl.security.dependencies import context_of
from atmpl.security.principals import parse_scopes
from atmpl.security.tokens import mint_access_token

router = APIRouter(tags=["auth"])


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    summary="Exchange client credentials for an access token",
    description=(
        "`grant_type=client_credentials`, form-encoded, as RFC 6749 specifies. The token "
        "is HS256, carries the granted `scope`, expires in at most 15 minutes, and names "
        "the signing key in its `kid` header. Tokens with an unknown `kid` are refused."
    ),
)
async def issue_token(
    request: Request,
    grant_type: str = Form(description="Must be `client_credentials`."),
    client_id: str = Form(),
    client_secret: str = Form(),
    scope: str | None = Form(default=None, description="Space-separated subset of the grant."),
) -> TokenResponse:
    context = context_of(request)
    if grant_type != "client_credentials":
        raise ApiError(
            400,
            "unsupported_grant_type",
            "Only the `client_credentials` grant is implemented.",
        )
    with context.engine.database.session() as session:
        client = authenticate_oauth_client(session, client_id, client_secret)
        if client is None:
            raise ApiError(401, "invalid_client", "Unknown client id or wrong client secret.")
        granted = frozenset(client.scopes)

    requested = parse_scopes(scope) if scope else granted
    excess = sorted(requested - granted)
    if excess:
        raise ApiError(
            400,
            "invalid_scope",
            f"Scope(s) not granted to this client: {', '.join(excess)}",
            details={"granted": sorted(granted)},
        )

    token, ttl = mint_access_token(
        subject=client_id,
        scopes=requested,
        key=context.jwt_key,
        key_id=context.settings.jwt_key_id,
        ttl_seconds=min(context.settings.token_ttl_seconds, 900),
    )
    return TokenResponse(access_token=token, expires_in=ttl, scope=" ".join(sorted(requested)))
