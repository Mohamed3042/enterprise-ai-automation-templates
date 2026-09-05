"""Inbound webhooks: an external event becomes a governed run, or nothing happens.

The mapping is declared per organization, not per request, so a caller cannot choose which
workflow it triggers, which region governs it, or which fields reach the model.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atmpl.engine.database import InboundEvent, Organization
from atmpl.guardrails.policy import DeterministicPolicyEngine
from atmpl.runtime import AppContext


class MappingNotConfigured(LookupError):
    """No organization declares an inbound mapping for this source."""


@dataclass(frozen=True)
class InboundMapping:
    source: str
    organization_id: str
    workflow_id: str
    title: str
    region: str
    field_map: dict[str, str]
    secret: str


def new_inbound_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(24)}"


def mapping_block(
    *,
    workflow_id: str,
    title: str,
    region: str,
    field_map: dict[str, str],
    secret: str | None = None,
) -> dict[str, Any]:
    """Build the org-profile block for one inbound source."""
    return {
        "workflow_id": workflow_id,
        "title": title,
        "region": region,
        "field_map": field_map,
        "secret": secret or new_inbound_secret(),
    }


def load_mapping(context: AppContext, source: str) -> InboundMapping:
    """Find the declared mapping. The secrets provider overrides the stored secret."""
    with context.engine.database.session() as session:
        for organization in session.scalars(select(Organization)):
            declared = (organization.profile or {}).get("webhooks", {}).get("inbound", {})
            block = declared.get(source)
            if not block:
                continue
            override = context.secrets.get(f"webhook_inbound_{source}")
            return InboundMapping(
                source=source,
                organization_id=organization.id,
                workflow_id=str(block["workflow_id"]),
                title=str(block.get("title", f"Inbound {source} event")),
                region=str(block.get("region", "GLOBAL")),
                field_map=dict(block.get("field_map", {})),
                secret=override or str(block["secret"]),
            )
    raise MappingNotConfigured(
        f"No organization declares webhooks.inbound['{source}'] in its profile."
    )


def _dig(payload: Any, dotted: str) -> Any:
    value = payload
    for part in dotted.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def project(mapping: InboundMapping, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the declared field map, then run the same G3 redaction the engine uses."""
    projected = {name: _dig(payload, path) for name, path in mapping.field_map.items()}
    preflight = DeterministicPolicyEngine().preflight(projected)
    return preflight.sanitized


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def find_replay(session: Session, source: str, idempotency_key: str) -> InboundEvent | None:
    return session.scalar(
        select(InboundEvent).where(
            InboundEvent.source == source,
            InboundEvent.idempotency_key == idempotency_key,
        )
    )
