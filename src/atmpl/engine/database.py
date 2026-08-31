"""SQLite persistence models for organizations, runs, stages, audits, and red-team evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
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


class Database:
    def __init__(self, url: str | None = None, path: Path | None = None) -> None:
        if url is None:
            db_path = path or Path.cwd() / "var" / "atmpl.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{db_path.as_posix()}"

        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        if url == "sqlite:///:memory:":
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

