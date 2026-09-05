"""Password hashing, API keys, and OAuth2 client secrets.

Nothing here stores a recoverable credential: passwords use PBKDF2-HMAC-SHA256 from the
standard library, and keys/secrets are kept as a salted SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from atmpl.engine.database import ApiKey, OAuthClient, utcnow
from atmpl.security.principals import Principal, PrincipalKind, parse_scopes

KEY_PREFIX = "atmpl_"
PBKDF2_ITERATIONS = 600_000
PBKDF2_SCHEME = "pbkdf2_sha256"


# --------------------------------------------------------------------------- passwords


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Return ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` (safe to put in an env var)."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return f"{PBKDF2_SCHEME}${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        scheme, iterations, salt, digest = encoded.split("$", 3)
        rounds = int(iterations)
    except ValueError:
        return False
    if scheme != PBKDF2_SCHEME:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds
    ).hex()
    return hmac.compare_digest(candidate, digest)


# --------------------------------------------------------------------------- shared


def _digest(value: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()


class CredentialError(ValueError):
    """A credential could not be created (duplicate name, unknown scope, ...)."""


# --------------------------------------------------------------------------- API keys


@dataclass(frozen=True)
class IssuedApiKey:
    id: str
    name: str
    scopes: list[str]
    token: str
    created_at: datetime


def create_api_key(session: Session, *, name: str, scopes: str | list[str]) -> IssuedApiKey:
    """Mint a key. The plaintext is returned once here and never stored."""
    parsed = sorted(parse_scopes(scopes))
    if not parsed:
        raise CredentialError("An API key needs at least one scope.")
    if session.scalar(select(ApiKey).where(ApiKey.name == name)):
        raise CredentialError(f"An API key named '{name}' already exists.")
    prefix = secrets.token_hex(4)
    token = f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    salt = secrets.token_hex(16)
    record = ApiKey(
        name=name,
        prefix=prefix,
        salt=salt,
        secret_hash=_digest(token, salt),
        scopes=parsed,
    )
    session.add(record)
    session.commit()
    return IssuedApiKey(
        id=record.id,
        name=record.name,
        scopes=parsed,
        token=token,
        created_at=record.created_at,
    )


def revoke_api_key(session: Session, name: str) -> bool:
    record = session.scalar(select(ApiKey).where(ApiKey.name == name))
    if not record or record.revoked_at is not None:
        return False
    record.revoked_at = utcnow()
    session.commit()
    return True


def authenticate_api_key(session: Session, token: str) -> Principal | None:
    if not token.startswith(KEY_PREFIX):
        return None
    body = token[len(KEY_PREFIX) :]
    prefix, _, _ = body.partition("_")
    if not prefix:
        return None
    for record in session.scalars(select(ApiKey).where(ApiKey.prefix == prefix)):
        if record.revoked_at is not None:
            continue
        if hmac.compare_digest(_digest(token, record.salt), record.secret_hash):
            record.last_used_at = utcnow()
            session.commit()
            return Principal(
                kind=PrincipalKind.CLIENT,
                subject=record.name,
                scopes=frozenset(record.scopes),
                credential_id=record.id,
            )
    return None


# --------------------------------------------------------------------------- OAuth2


@dataclass(frozen=True)
class IssuedOAuthClient:
    client_id: str
    name: str
    scopes: list[str]
    client_secret: str


def create_oauth_client(
    session: Session,
    *,
    name: str,
    scopes: str | list[str],
    client_id: str | None = None,
) -> IssuedOAuthClient:
    parsed = sorted(parse_scopes(scopes))
    if not parsed:
        raise CredentialError("An OAuth client needs at least one scope.")
    client_id = client_id or f"cli_{secrets.token_hex(8)}"
    if session.get(OAuthClient, client_id):
        raise CredentialError(f"OAuth client '{client_id}' already exists.")
    client_secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    session.add(
        OAuthClient(
            id=client_id,
            name=name,
            salt=salt,
            secret_hash=_digest(client_secret, salt),
            scopes=parsed,
        )
    )
    session.commit()
    return IssuedOAuthClient(
        client_id=client_id,
        name=name,
        scopes=parsed,
        client_secret=client_secret,
    )


def authenticate_oauth_client(
    session: Session,
    client_id: str,
    client_secret: str,
) -> OAuthClient | None:
    record = session.get(OAuthClient, client_id)
    if not record or record.revoked_at is not None:
        return None
    if not hmac.compare_digest(_digest(client_secret, record.salt), record.secret_hash):
        return None
    record.last_used_at = utcnow()
    session.commit()
    return record
