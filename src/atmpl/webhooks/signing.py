"""One signature scheme for both directions, so a receiver can verify what we send.

Header: ``X-ATMPL-Signature: t=<unix seconds>,v1=<hex hmac-sha256>``
Signed string: ``<t>.<raw request body>`` — the timestamp is inside the MAC, so it cannot
be moved without breaking the signature, and a replay outside the tolerance is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-ATMPL-Signature"
SCHEME_VERSION = "v1"


class SignatureError(ValueError):
    """The presented signature is missing, malformed, stale, or wrong."""


def signed_payload(timestamp: int, body: bytes) -> bytes:
    return f"{timestamp}.".encode() + body


def compute(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"), signed_payload(timestamp, body), hashlib.sha256
    ).hexdigest()


def sign(secret: str, body: bytes, *, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    return f"t={timestamp},{SCHEME_VERSION}={compute(secret, timestamp, body)}"


def parse(header: str) -> tuple[int, str]:
    parts = dict(
        item.split("=", 1) for item in header.split(",") if "=" in item
    )
    if "t" not in parts or SCHEME_VERSION not in parts:
        raise SignatureError(f"Signature header must be 't=<unix>,{SCHEME_VERSION}=<hex>'.")
    try:
        return int(parts["t"]), parts[SCHEME_VERSION]
    except ValueError as exc:
        raise SignatureError("Signature timestamp is not an integer.") from exc


def verify(
    secret: str,
    header: str | None,
    body: bytes,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> int:
    """Return the signed timestamp, or raise :class:`SignatureError`."""
    if not header:
        raise SignatureError(f"Missing {SIGNATURE_HEADER} header.")
    timestamp, presented = parse(header)
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > tolerance_seconds:
        raise SignatureError(
            f"Signature timestamp is outside the {tolerance_seconds}s replay tolerance."
        )
    if not hmac.compare_digest(compute(secret, timestamp, body), presented):
        raise SignatureError("Signature does not match the request body.")
    return timestamp
