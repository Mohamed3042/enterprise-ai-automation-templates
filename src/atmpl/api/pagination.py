"""Opaque cursor pagination. The cursor carries the last row's sort key, nothing else."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from atmpl.api.errors import ApiError

DEFAULT_LIMIT = 25
MAX_LIMIT = 200


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any]:
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError(400, "invalid_cursor", "The `cursor` value is not a cursor.") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_cursor", "The `cursor` value is not a cursor.")
    return payload


def encode_row_cursor(created_at: datetime, row_id: str) -> str:
    """Cursors are ordered by (created_at, id) so rows with equal timestamps still page."""
    return encode_cursor({"at": created_at.isoformat(), "id": row_id})


def decode_row_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    marker = decode_cursor(cursor)
    if not marker:
        return None
    try:
        return datetime.fromisoformat(str(marker["at"])), str(marker["id"])
    except (KeyError, ValueError) as exc:
        raise ApiError(400, "invalid_cursor", "The `cursor` value is not a cursor.") from exc


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(
        default=None,
        description="Pass back as `cursor` for the next page. Null on the last page.",
    )
    has_more: bool = False
