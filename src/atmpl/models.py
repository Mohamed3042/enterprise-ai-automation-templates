"""Typed domain models shared by templates, discovery, execution, and approvals."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def question(text: str) -> dict[str, str]:
    return {"question": text}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskTier(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActionType(StrEnum):
    AI_DRAFT = "ai_draft"
    AI_EXTRACT = "ai_extract"
    AI_CLASSIFY = "ai_classify"
    HUMAN_REVIEW = "human_review"
    HUMAN_APPROVE = "human_approve"
    SYSTEM_ACTION = "system_action"
    NOTIFY = "notify"


AI_ACTIONS = {ActionType.AI_DRAFT, ActionType.AI_EXTRACT, ActionType.AI_CLASSIFY}


class StageStatus(StrEnum):
    PENDING = "pending"
    DRAFT_READY = "draft_ready"
    COMPLETED = "completed"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    PARKED = "parked"


class DecisionValue(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class DataSensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RiskAppetite(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"


class OrganizationPlaceholders(StrictModel):
    name: str = Field(
        min_length=2,
        json_schema_extra=question("What organization name should appear in the workflow?"),
    )
    process_owner: str = Field(
        min_length=2,
        json_schema_extra=question("Who owns the process and its operating outcome?"),
    )
    monthly_volume: int = Field(
        ge=1,
        le=10_000_000,
        json_schema_extra=question("How many cases does this process handle per month?"),
    )
    data_sensitivity: DataSensitivity = Field(
        json_schema_extra=question("What is the highest data-sensitivity level in scope?"),
    )
    locales: list[str] = Field(
        min_length=1,
        json_schema_extra=question("Which locale codes must the workflow support?"),
    )


class GovernancePlaceholders(StrictModel):
    approval_authority_matrix: dict[str, str] = Field(
        min_length=1,
        json_schema_extra=question(
            "Which named roles may approve each consequential decision or amount band?"
        ),
    )
    risk_appetite: RiskAppetite = Field(
        json_schema_extra=question(
            "Is the agreed operating risk appetite conservative or balanced?"
        ),
    )
    escalation_owner: str = Field(
        min_length=2,
        json_schema_extra=question("Which role receives blocked or out-of-policy cases?"),
    )


class RetailRegionalPlaceholders(StrictModel):
    region: Literal["EU", "US", "GCC"] = Field(
        json_schema_extra=question("Which regional policy boundary applies: EU, US, or GCC?"),
    )
    policy_pack: Literal["eu_gdpr_14_day", "us_standard", "gcc_consumer"] = Field(
        json_schema_extra=question("Which approved regional policy pack must be enforced?"),
    )
    refund_limit: float = Field(
        gt=0,
        le=100_000,
        json_schema_extra=question("What is the maximum refund value in local currency?"),
    )
    currency: Literal["EUR", "USD", "KWD"] = Field(
        json_schema_extra=question("Which currency is used for this regional workflow?"),
    )


class CurriculumPlaceholders(StrictModel):
    framework: str = Field(
        min_length=2,
        json_schema_extra=question("Which curriculum framework must lesson plans align to?"),
    )
    department_head_role: str = Field(
        min_length=2,
        json_schema_extra=question("Which role provides department-head approval?"),
    )
    qa_role: str = Field(
        min_length=2,
        json_schema_extra=question("Which role owns the final quality gate?"),
    )
    publish_channel: str = Field(
        min_length=2,
        json_schema_extra=question("Where are approved lesson plans published?"),
    )


class CreditPlaceholders(StrictModel):
    authority_bands: dict[str, str] = Field(
        min_length=1,
        json_schema_extra=question("Which amount bands map to which credit-officer tiers?"),
    )
    maximum_application_amount: float = Field(
        gt=0,
        le=100_000_000,
        json_schema_extra=question("What is the maximum application amount in scope?"),
    )
    completeness_threshold: int = Field(
        ge=1,
        le=100,
        json_schema_extra=question("What completeness score is required before human triage?"),
    )
    terminal_decision_role: str = Field(
        min_length=2,
        json_schema_extra=question("Which human role owns every terminal credit decision?"),
    )


class RetailPlaceholders(StrictModel):
    organization: OrganizationPlaceholders
    governance: GovernancePlaceholders
    regional: RetailRegionalPlaceholders


class MinistryPlaceholders(StrictModel):
    organization: OrganizationPlaceholders
    governance: GovernancePlaceholders
    curriculum: CurriculumPlaceholders


class BankPlaceholders(StrictModel):
    organization: OrganizationPlaceholders
    governance: GovernancePlaceholders
    credit: CreditPlaceholders


class TemplateMetadata(StrictModel):
    id: str
    name: str
    version: str
    sector: str
    description: str


class ActorDefinition(StrictModel):
    role: str
    responsibility: str


class StageDefinition(StrictModel):
    id: str
    name: str
    action_type: ActionType
    risk_tier: RiskTier
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    escalation_rules: list[str] = Field(default_factory=list)
    sla_hours: int = Field(ge=1, le=720)
    audit_tags: list[str] = Field(default_factory=list)
    approval_roles: list[str] = Field(default_factory=list)


class TemplateDocument(StrictModel):
    metadata: TemplateMetadata
    actors: list[ActorDefinition]
    stages: list[StageDefinition]


class InstantiatedWorkflow(TemplateDocument):
    source_template: str
    org_profile: dict[str, Any]


class SignedHumanDecision(StrictModel):
    actor: str = Field(min_length=2)
    role: str = Field(min_length=2)
    decision: DecisionValue
    reason: str = Field(min_length=3)
    signature: str = Field(min_length=8)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicyViolation(StrictModel):
    guardrail: str
    code: str
    message: str


class PolicyResult(StrictModel):
    allowed: bool
    sanitized: dict[str, Any]
    violations: list[PolicyViolation] = Field(default_factory=list)


class LLMOutput(StrictModel):
    task: str
    input_hash: str
    content: dict[str, Any]
    adapter: str


class EngineResult(StrictModel):
    accepted: bool
    status: StageStatus
    guardrail: str | None = None
    detail: str
    output: dict[str, Any] | None = None
