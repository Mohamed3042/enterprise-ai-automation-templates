"""Relational persistence for organizations, runs, stages, audit, credentials, and webhooks.

SQLite is the keyless default; any SQLAlchemy URL works, and PostgreSQL is a first-class
target (``ATMPL_DATABASE_URL=postgresql+psycopg://...`` plus ``atmpl db upgrade``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    sector: Mapped[str] = mapped_column(String(80))
    profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workflows: Mapped[list[Workflow]] = relationship(back_populates="organization")
    runs: Mapped[list[Run]] = relationship(back_populates="organization")


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[str] = mapped_column(String(100))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization: Mapped[Organization] = relationship(back_populates="workflows")
    runs: Mapped[list[Run]] = relationship(back_populates="workflow")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    title: Mapped[str] = mapped_column(String(250))
    region: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40), default="active")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workflow: Mapped[Workflow] = relationship(back_populates="runs")
    organization: Mapped[Organization] = relationship(back_populates="runs")
    stages: Mapped[list[Stage]] = relationship(
        back_populates="run",
        order_by="Stage.position",
        cascade="all, delete-orphan",
    )


class Stage(Base):
    __tablename__ = "stages"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("stage"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    stage_key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(40))
    risk_tier: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[Run] = relationship(back_populates="stages")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(80), unique=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100))
    organization_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stage_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor: Mapped[str] = mapped_column(String(150))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64), unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RedTeamResult(Base):
    __tablename__ = "redteam_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(10))
    demo: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    expectation: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)


class ApiKey(Base):
    """A machine credential. Only a salted SHA-256 of the key is ever stored."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("key"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    salt: Mapped[str] = mapped_column(String(64))
    secret_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthClient(Base):
    """An OAuth2 client-credentials principal. Only a salted hash of the secret is stored."""

    __tablename__ = "oauth_clients"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    salt: Mapped[str] = mapped_column(String(64))
    secret_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookSubscription(Base):
    """Where governed events are delivered, and which events this receiver wants."""

    __tablename__ = "webhook_subscriptions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("whs"))
    url: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(250), default="")
    secret: Mapped[str] = mapped_column(String(120))
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    deliveries: Mapped[list[WebhookDelivery]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )


class OutboxEvent(Base):
    """Written in the same transaction as the state change it describes."""

    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("evt"))
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    organization_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stage_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fanned_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookDelivery(Base):
    """One attempt log per (event, subscription) pair, ending delivered or dead_letter."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("whd"))
    subscription_id: Mapped[str] = mapped_column(ForeignKey("webhook_subscriptions.id"))
    event_id: Mapped[str] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subscription: Mapped[WebhookSubscription] = relationship(back_populates="deliveries")


class InboundEvent(Base):
    """Idempotency ledger for inbound webhooks: one key per source, replayed verbatim."""

    __tablename__ = "inbound_events"
    __table_args__ = (UniqueConstraint("source", "idempotency_key", name="uq_inbound_source_key"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("inb"))
    source: Mapped[str] = mapped_column(String(80), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    body_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiscoverySession(Base):
    """A consultant discovery pack in progress: questions asked, answers so far."""

    __tablename__ = "discovery_sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=lambda: new_id("dsc"))
    template: Mapped[str] = mapped_column(String(80))
    organization: Mapped[str] = mapped_column(String(200))
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    workflow_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Database:
    def __init__(
        self,
        url: str | None = None,
        path: Path | None = None,
        *,
        create_all: bool = True,
    ) -> None:
        if url is None:
            db_path = path or Path.cwd() / "var" / "atmpl.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{db_path.as_posix()}"
        elif url.startswith("sqlite:///") and not url.endswith(":memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

        self.url = url
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        if url == "sqlite:///:memory:":
            kwargs["poolclass"] = StaticPool
        else:
            kwargs["pool_pre_ping"] = True
        self.engine = create_engine(url, **kwargs)
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        if create_all:
            Base.metadata.create_all(self.engine)
