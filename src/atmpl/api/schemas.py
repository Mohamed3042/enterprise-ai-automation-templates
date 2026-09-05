"""Request and response models for `/api/v1`. These are the API's contract, not the ORM."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from atmpl.models import DecisionValue

TEMPLATE_KEYS = Literal["retail", "ministry", "bank"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- templates


class TemplateSummary(BaseModel):
    key: str = Field(description="Canonical template id, e.g. `retail_refund`.")
    aliases: list[str] = Field(description="Short names accepted by the API and CLI.")
    name: str
    sector: str
    version: str
    description: str
    stage_count: int


class ActorOut(BaseModel):
    role: str
    responsibility: str


class StageDefinitionOut(BaseModel):
    id: str
    name: str
    action_type: str
    risk_tier: str
    sla_hours: int
    approval_roles: list[str]
    escalation_rules: list[str]


class TemplateDetail(TemplateSummary):
    actors: list[ActorOut]
    stages: list[StageDefinitionOut]
    placeholder_schema: dict[str, Any] = Field(
        description="JSON Schema for the typed discovery answers this template needs."
    )


# --------------------------------------------------------------------------- discovery


class DiscoverySessionCreate(ApiModel):
    template: TEMPLATE_KEYS
    organization: str = Field(min_length=2, max_length=200)


class QuestionOut(BaseModel):
    placeholder: str
    question: str
    type: str


class FollowUpOut(BaseModel):
    placeholder: str
    question: str
    problem: str


class DiscoverySessionOut(BaseModel):
    id: str
    template: str
    organization: str
    questions: list[QuestionOut]
    answers: dict[str, Any]
    resolved: bool
    workflow_id: str | None = None
    created_at: datetime


class AnswersUpdate(ApiModel):
    answers: dict[str, Any]


class AnswersValidation(BaseModel):
    id: str
    valid: bool = Field(description="True when the answers compile with no follow-up left.")
    follow_ups: list[FollowUpOut]


class ResolveRequest(ApiModel):
    organization_id: str | None = Field(
        default=None,
        description="Reuse an existing organization id instead of deriving one from the name.",
    )


class ResolvedWorkflowOut(BaseModel):
    session_id: str
    organization_id: str
    workflow_id: str
    template: str
    name: str
    stage_count: int
    org_profile: dict[str, Any]
    workflow: dict[str, Any]


# --------------------------------------------------------------------------- runs


class RunCreate(ApiModel):
    workflow_id: str | None = Field(default=None, description="A compiled workflow to start.")
    discovery_session_id: str | None = Field(
        default=None,
        description="Alternative to `workflow_id`: start the workflow this session resolved.",
    )
    title: str = Field(min_length=3, max_length=250)
    region: str | None = Field(default=None, max_length=30)
    evidence: dict[str, Any] | None = Field(
        default=None,
        description="Attached to the first stage as recorded evidence for the run.",
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> RunCreate:
        provided = [bool(self.workflow_id), bool(self.discovery_session_id)]
        if sum(provided) != 1:
            raise ValueError("Provide exactly one of `workflow_id` or `discovery_session_id`.")
        return self


class StageOut(BaseModel):
    id: str
    stage_key: str
    name: str
    position: int
    action_type: str
    risk_tier: str
    status: str
    allowed_roles: list[str]
    escalation_reason: str | None = None
    draft: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None


class RunSummary(BaseModel):
    id: str
    title: str
    region: str
    status: str
    synthetic: bool
    organization_id: str
    workflow_id: str
    created_at: datetime


class RunDetail(RunSummary):
    organization_name: str
    workflow_name: str
    stages: list[StageOut]


# --------------------------------------------------------------------------- decisions


class DecisionRequest(ApiModel):
    actor: str = Field(min_length=2, description="The named human who decided.")
    role: str = Field(min_length=2, description="Must appear in the stage authority matrix.")
    decision: DecisionValue
    reason: str = Field(min_length=3)
    signature: str = Field(min_length=8)


class DecisionResult(BaseModel):
    accepted: bool
    status: str
    guardrail: str | None = None
    detail: str
    principal: str = Field(description="The authenticated identity written into the ledger.")


# --------------------------------------------------------------------------- audit


class AuditEntryOut(BaseModel):
    event_id: str
    sequence: int
    event_type: str
    actor: str
    organization_id: str | None = None
    run_id: str | None = None
    stage_id: str | None = None
    payload: dict[str, Any]
    prev_hash: str
    record_hash: str
    timestamp: datetime


class AuditVerificationOut(BaseModel):
    valid: bool
    count: int
    last_hash: str
    error: str | None = None


# --------------------------------------------------------------------------- ops


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    adapter: str


class ReadyOut(BaseModel):
    status: Literal["ready", "degraded"]
    database: Literal["reachable", "unreachable"]
    detail: str | None = None


# --------------------------------------------------------------------------- oauth


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    scope: str


# --------------------------------------------------------------------------- webhooks


class SubscriptionCreate(ApiModel):
    url: HttpUrl
    event_types: list[str] = Field(
        default_factory=list,
        description="Empty means every event. e.g. `run.stage.decided`, `guardrail.blocked`.",
    )
    description: str = Field(default="", max_length=250)


class SubscriptionOut(BaseModel):
    id: str
    url: str
    description: str
    event_types: list[str]
    active: bool
    created_at: datetime
    secret: str | None = Field(
        default=None,
        description="Returned only on creation. Store it; it is never shown again.",
    )


class DeliveryOut(BaseModel):
    id: str
    subscription_id: str
    event_id: str
    event_type: str
    status: str
    attempts: int
    last_status_code: int | None = None
    last_latency_ms: float | None = None
    last_response: str | None = None
    receipts: list[dict[str, Any]]
    next_attempt_at: datetime
    created_at: datetime
    updated_at: datetime


class InboundAck(BaseModel):
    accepted: bool
    source: str
    run_id: str | None = None
    idempotent_replay: bool = False
    detail: str
