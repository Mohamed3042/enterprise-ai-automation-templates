"""Short-lived HS256 access tokens and signed dashboard session cookies."""

from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import jwt

ISSUER = "atmpl"
AUDIENCE = "atmpl-api"


class TokenError(ValueError):
    """A presented token is malformed, expired, or signed with an unknown key."""


# --------------------------------------------------------------------------- JWT


def mint_access_token(
    *,
    subject: str,
    scopes: list[str] | frozenset[str],
    key: str,
    key_id: str,
    ttl_seconds: int,
) -> tuple[str, int]:
    now = datetime.now(UTC)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": subject,
        "scope": " ".join(sorted(scopes)),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, key, algorithm="HS256", headers={"kid": key_id})
    return token, ttl_seconds


def read_access_token(token: str, *, key: str, key_id: str) -> dict:
    """Decode a bearer JWT. An unknown ``kid`` is refused before any signature check."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError("Malformed access token.") from exc
    if header.get("kid") != key_id:
        raise TokenError(f"Unknown signing key id '{header.get('kid')}'.")
    try:
        return jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Access token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Access token is not valid.") from exc


# --------------------------------------------------------------------------- sessions


@dataclass(frozen=True)
class SessionData:
    user: str
    csrf: str
    expires_at: int


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def sign_session(data: SessionData, *, key: str) -> str:
    body = _b64(
        json.dumps(
            {"user": data.user, "csrf": data.csrf, "exp": data.expires_at},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = hmac.new(key.encode("utf-8"), body.encode("ascii"), sha256).digest()
    return f"{body}.{_b64(signature)}"


def read_session(cookie: str, *, key: str) -> SessionData:
    try:
        body, _, signature = cookie.partition(".")
        if not body or not signature:
            raise ValueError("missing signature")
        expected = hmac.new(key.encode("utf-8"), body.encode("ascii"), sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            raise ValueError("bad signature")
        payload = json.loads(_unb64(body))
        session = SessionData(
            user=str(payload["user"]),
            csrf=str(payload["csrf"]),
            expires_at=int(payload["exp"]),
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TokenError("Session cookie is not valid.") from exc
    if session.expires_at < int(datetime.now(UTC).timestamp()):
        raise TokenError("Session has expired.")
    return session
