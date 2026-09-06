"""Who is acting, and what they are allowed to do.

Every consequential transition names a principal, and that name reaches the hash-chained
ledger: ``human:<user>`` for a dashboard login, ``client:<id>`` for a machine credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    TEMPLATES_READ = "templates:read"
    DISCOVERY_WRITE = "discovery:write"
    RUNS_READ = "runs:read"
    RUNS_WRITE = "runs:write"
    DECISIONS_WRITE = "decisions:write"
    AUDIT_READ = "audit:read"
    WEBHOOKS_MANAGE = "webhooks:manage"
    METRICS_READ = "metrics:read"


ALL_SCOPES: frozenset[str] = frozenset(scope.value for scope in Scope)


class PrincipalKind(StrEnum):
    HUMAN = "human"
    CLIENT = "client"
    DEMO = "demo"


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    subject: str
    scopes: frozenset[str]
    credential_id: str | None = None

    @property
    def actor(self) -> str:
        """The string written into the audit ledger."""
        prefix = "human" if self.kind is PrincipalKind.DEMO else self.kind.value
        return f"{prefix}:{self.subject}"

    @property
    def is_human(self) -> bool:
        return self.kind in {PrincipalKind.HUMAN, PrincipalKind.DEMO}

    def has(self, scope: str) -> bool:
        return scope in self.scopes


DEMO_PRINCIPAL = Principal(
    kind=PrincipalKind.DEMO,
    subject="demo",
    scopes=ALL_SCOPES,
    credential_id=None,
)


def parse_scopes(raw: str | list[str] | None) -> frozenset[str]:
    """Accept ``"runs:read runs:write"``, ``"runs:read,runs:write"`` or a list."""
    if raw is None:
        return frozenset()
    items = raw.replace(",", " ").split() if isinstance(raw, str) else list(raw)
    unknown = sorted(set(items) - ALL_SCOPES)
    if unknown:
        raise ValueError(
            f"Unknown scope(s): {', '.join(unknown)}. Allowed: {', '.join(sorted(ALL_SCOPES))}"
        )
    return frozenset(items)
