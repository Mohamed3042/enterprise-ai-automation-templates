"""The deterministic, offline, keyless provider — still the default.

Same input, same bytes, no network, no key. Every seeded demo, every CI run and every
red-team case goes through this provider, which is why the repository can promise a
reproducible walkthrough. It answers two kinds of work order:

* the drafting tasks the engine sends (identical content to the v0.1 mock adapter, so the
  audit hashes of the seeded demos do not move), and
* the discovery agent's questionnaire, where it returns one fixed, *valid* answer set and
  makes exactly one tool call on the way, so the agent's tool loop is exercised offline.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from atmpl.providers.base import (
    Message,
    ProviderResult,
    ToolCall,
    ToolSpec,
    Usage,
    render_prompt,
)

MODEL_NAME = "deterministic-v1"
TASK_MARKER = "atmpl-task:"
ORG_MARKER = "atmpl-organization:"
TEMPLATE_MARKER = "atmpl-template:"

#: One fixed, valid answer per template, in the agent's wire shape (authority maps as
#: `{key, value}` rows). Values are synthetic and deliberately boring; the point of the
#: mock is that the *shape* is right every time, not that it is clever.
DISCOVERY_ANSWERS: dict[str, dict[str, Any]] = {
    "retail_refund": {
        "organization": {
            "name": "Synthetic Organization",
            "process_owner": "customer_operations_lead",
            "monthly_volume": 4200,
            "data_sensitivity": "confidential",
            "locales": ["en-GB", "ar-KW"],
        },
        "governance": {
            "approval_authority_matrix": [
                {"key": "refund_within_limit", "value": "store_manager"},
                {"key": "refund_above_limit", "value": "regional_director"},
            ],
            "risk_appetite": "conservative",
            "escalation_owner": "regional_director",
        },
        "regional": {
            "region": "EU",
            "policy_pack": "eu_gdpr_14_day",
            "refund_limit": 250.0,
            "currency": "EUR",
        },
    },
    "lesson_preparation": {
        "organization": {
            "name": "Synthetic Organization",
            "process_owner": "curriculum_director",
            "monthly_volume": 900,
            "data_sensitivity": "internal",
            "locales": ["ar-KW", "en-GB"],
        },
        "governance": {
            "approval_authority_matrix": [
                {"key": "lesson_plan", "value": "department_head"},
                {"key": "publication", "value": "quality_assurance_lead"},
            ],
            "risk_appetite": "conservative",
            "escalation_owner": "quality_assurance_lead",
        },
        "curriculum": {
            "framework": "National Curriculum Framework",
            "department_head_role": "department_head",
            "qa_role": "quality_assurance_lead",
            "publish_channel": "Teacher Portal",
        },
    },
    "loan_triage": {
        "organization": {
            "name": "Synthetic Organization",
            "process_owner": "credit_operations_lead",
            "monthly_volume": 1800,
            "data_sensitivity": "restricted",
            "locales": ["en-GB", "ar-KW"],
        },
        "governance": {
            "approval_authority_matrix": [
                {"key": "tier_1_triage", "value": "credit_officer"},
                {"key": "terminal_decision", "value": "senior_credit_officer"},
            ],
            "risk_appetite": "conservative",
            "escalation_owner": "credit_risk_committee",
        },
        "credit": {
            "authority_bands": [
                {"key": "0-25000", "value": "credit_officer"},
                {"key": "25001-250000", "value": "senior_credit_officer"},
            ],
            "maximum_application_amount": 250000.0,
            "completeness_threshold": 80,
            "terminal_decision_role": "senior_credit_officer",
        },
    },
}

#: Why the mock chose each value. Deterministic, and honest about being a fixture.
DISCOVERY_RATIONALES: dict[str, list[tuple[str, str]]] = {
    "retail_refund": [
        ("organization.process_owner", "the described owner of the refund desk"),
        ("organization.monthly_volume", "the described monthly case volume"),
        ("organization.data_sensitivity", "customer order records are in scope"),
        ("organization.locales", "the described service languages"),
        ("governance.approval_authority_matrix", "two approval bands were described"),
        ("governance.risk_appetite", "a regulated consumer process"),
        ("governance.escalation_owner", "named as the escalation owner"),
        ("regional.region", "the described operating region"),
        ("regional.policy_pack", "the only approved pack for that region"),
        ("regional.refund_limit", "the stated auto-approval ceiling"),
        ("regional.currency", "follows the region"),
    ],
    "lesson_preparation": [
        ("organization.process_owner", "the described owner of lesson preparation"),
        ("organization.monthly_volume", "lesson plans prepared each month"),
        ("organization.data_sensitivity", "no pupil records are in scope"),
        ("organization.locales", "the described teaching languages"),
        ("governance.approval_authority_matrix", "two review gates were described"),
        ("governance.risk_appetite", "a public-sector publication process"),
        ("governance.escalation_owner", "owns the final quality gate"),
        ("curriculum.framework", "the named curriculum framework"),
        ("curriculum.department_head_role", "the named approver"),
        ("curriculum.qa_role", "the named final gate"),
        ("curriculum.publish_channel", "the stated publication channel"),
    ],
    "loan_triage": [
        ("organization.process_owner", "the described owner of credit operations"),
        ("organization.monthly_volume", "applications received each month"),
        ("organization.data_sensitivity", "credit files are in scope"),
        ("organization.locales", "the described service languages"),
        ("governance.approval_authority_matrix", "two officer tiers were described"),
        ("governance.risk_appetite", "a regulated lending process"),
        ("governance.escalation_owner", "the named escalation body"),
        ("credit.authority_bands", "the described amount bands"),
        ("credit.maximum_application_amount", "the stated ceiling"),
        ("credit.completeness_threshold", "the stated completeness bar"),
        ("credit.terminal_decision_role", "owns every terminal decision"),
    ],
}


def _marker(prompt: str, marker: str) -> str | None:
    match = re.search(rf"{re.escape(marker)}\s*(.+)", prompt)
    return match.group(1).strip() if match else None


def draft_content(task: str) -> dict[str, Any]:
    """The v0.1 mock adapter's answer, unchanged, so seeded demo hashes stay stable."""
    task_lower = task.lower()
    if "loan" in task_lower or "credit" in task_lower:
        return {
            "summary": "Synthetic application is complete enough for officer review.",
            "recommendation": "REFER_TO_TIER_2",
            "completeness_score": 86,
            "risk_flags": ["income_variance"],
        }
    if "lesson" in task_lower or "curriculum" in task_lower:
        return {
            "summary": "Draft enrichment aligned to the selected synthetic curriculum profile.",
            "recommendation": "DEPARTMENT_REVIEW",
            "alignment_score": 92,
            "bilingual": True,
        }
    return {
        "summary": "Synthetic request classified for governed human review.",
        "recommendation": "REVIEW",
        "confidence": 0.88,
    }


def discovery_draft(template: str, organization: str) -> dict[str, Any]:
    """A complete, valid discovery draft — the same one, every time, for this template."""
    key = template if template in DISCOVERY_ANSWERS else "retail_refund"
    answers = copy.deepcopy(DISCOVERY_ANSWERS[key])
    answers["organization"]["name"] = organization
    return {
        "answers": answers,
        "rationales": [
            {
                "placeholder": placeholder,
                "rationale": f"Deterministic mock: {reason}.",
                "confidence": "inferred",
            }
            for placeholder, reason in DISCOVERY_RATIONALES[key]
        ],
        "open_questions": [],
    }


class MockProvider:
    """Deterministic offline provider. Never opens a socket."""

    name = "mock"

    def __init__(self, model: str = MODEL_NAME) -> None:
        self.model = model

    def configured(self) -> bool:
        return True

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ProviderResult:
        prompt = render_prompt(messages)
        task = _marker(prompt, TASK_MARKER) or "unspecified"
        tool_names = {tool.name for tool in tools or ()}

        # One tool call on the first turn, so the agent's tool loop runs offline too.
        turn_taken = any(message.role in {"assistant", "tool"} for message in messages)
        if not turn_taken and "list_required_fields" in tool_names:
            template = _marker(prompt, TEMPLATE_MARKER) or "retail_refund"
            return self._result(
                prompt,
                tool_calls=(ToolCall("list_required_fields", {"template": template}),),
            )

        if task.startswith("discovery."):
            template = _marker(prompt, TEMPLATE_MARKER) or "retail_refund"
            organization = _marker(prompt, ORG_MARKER) or "Synthetic Organization"
            return self._result(prompt, json=discovery_draft(template, organization))

        return self._result(prompt, json=draft_content(task))

    def _result(
        self,
        prompt: str,
        *,
        json: dict[str, Any] | None = None,
        tool_calls: tuple[ToolCall, ...] = (),
    ) -> ProviderResult:
        rendered = _dumps(json) if json is not None else ""
        return ProviderResult(
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            text=rendered or None,
            json=json,
            tool_calls=tool_calls,
            # A deterministic provider has no billed tokens; the counts are the honest
            # size of what it processed, so the LLMOps table is not a column of blanks.
            usage=Usage(
                input_tokens=_approx_tokens(prompt),
                output_tokens=_approx_tokens(rendered or _dumps(tool_calls)),
            ),
            finish_reason="stop",
        )


def _dumps(value: Any) -> str:
    return json_dumps(value)


def json_dumps(value: Any) -> str:
    if isinstance(value, tuple):
        value = [{"name": call.name, "arguments": call.arguments} for call in value]
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _approx_tokens(text: str) -> int:
    """Whitespace tokens. Labelled `approximate` wherever it is displayed."""
    return len(text.split())


def input_hash(task: str, payload: dict[str, Any]) -> str:
    """The v0.1 hash of a drafting request, kept so audit records stay comparable."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{task}:{canonical}".encode()).hexdigest()
