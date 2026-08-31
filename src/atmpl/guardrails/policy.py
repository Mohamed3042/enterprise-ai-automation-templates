"""Deterministic pre/post policy checks; no model participates in enforcement."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from atmpl.guardrails.constants import G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER
from atmpl.models import PolicyResult, PolicyViolation

INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"reveal\s+(the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"approve\s+this\s+(loan|refund|request)", re.IGNORECASE),
    re.compile(r"bypass\s+(policy|approval|guardrail)", re.IGNORECASE),
)
SENSITIVE_KEYS = {
    "ssn",
    "national_id",
    "civil_id",
    "account_number",
    "card_number",
    "customer_email",
}
EXFILTRATION_PATTERNS = (
    re.compile(r"\[EXFILTRATED_PII\]", re.IGNORECASE),
    re.compile(r"another\s+customer", re.IGNORECASE),
    re.compile(r"customer[_-]?999@", re.IGNORECASE),
)


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class DeterministicPolicyEngine:
    """Fail-closed G3 policy rules run on both sides of the adapter boundary."""

    def preflight(
        self,
        payload: dict[str, Any],
        *,
        allowed_region: str | None = None,
        allowed_scope: set[str] | None = None,
    ) -> PolicyResult:
        sanitized = _redact(copy.deepcopy(payload))
        violations: list[PolicyViolation] = []
        text = _flatten_text(payload)

        if any(pattern.search(text) for pattern in INJECTION_PATTERNS):
            violations.append(
                PolicyViolation(
                    guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                    code="PROMPT_INJECTION",
                    message="Instruction-like content was quarantined before adapter access.",
                )
            )

        submitted_region = payload.get("region")
        policy_region = payload.get("policy_region")
        if allowed_region and submitted_region and submitted_region != allowed_region:
            violations.append(
                PolicyViolation(
                    guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                    code="REGIONAL_BOUNDARY",
                    message=(
                        f"Record region {submitted_region} cannot use {allowed_region} routing."
                    ),
                )
            )
        if allowed_region and policy_region and policy_region != allowed_region:
            violations.append(
                PolicyViolation(
                    guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                    code="POLICY_PACK_MISMATCH",
                    message=f"Policy region {policy_region} does not match {allowed_region}.",
                )
            )

        requested_action = payload.get("requested_action")
        if allowed_scope and requested_action and requested_action not in allowed_scope:
            violations.append(
                PolicyViolation(
                    guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                    code="OUT_OF_SCOPE",
                    message=f"Action '{requested_action}' is not in the approved workflow scope.",
                )
            )

        return PolicyResult(
            allowed=not violations,
            sanitized=sanitized,
            violations=violations,
        )

    def postflight(
        self,
        output: Any,
        *,
        required_fields: set[str] | None = None,
        numeric_bounds: dict[str, tuple[float, float]] | None = None,
        max_bytes: int = 4096,
    ) -> PolicyResult:
        violations: list[PolicyViolation] = []
        if not isinstance(output, dict):
            violations.append(
                PolicyViolation(
                    guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                    code="OUTPUT_SCHEMA",
                    message="Adapter output must be a JSON object.",
                )
            )
            sanitized: dict[str, Any] = {}
        else:
            sanitized = copy.deepcopy(output)

        encoded = json.dumps(output, ensure_ascii=False, default=str).encode("utf-8")
        if len(encoded) > max_bytes:
            violations.append(
                PolicyViolation(
                    guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                    code="OUTPUT_OVERSIZED",
                    message=f"Adapter output exceeds the {max_bytes}-byte bound.",
                )
            )

        text = _flatten_text(output)
        if any(pattern.search(text) for pattern in EXFILTRATION_PATTERNS):
            violations.append(
                PolicyViolation(
                    guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                    code="FORBIDDEN_CONTENT",
                    message="Output contains synthetic cross-customer PII or exfiltration markers.",
                )
            )

        if isinstance(output, dict):
            missing = sorted((required_fields or set()) - output.keys())
            if missing:
                violations.append(
                    PolicyViolation(
                        guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                        code="OUTPUT_SCHEMA",
                        message=f"Adapter output is missing required fields: {', '.join(missing)}.",
                    )
                )
            for field, (minimum, maximum) in (numeric_bounds or {}).items():
                value = output.get(field)
                if not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                    violations.append(
                        PolicyViolation(
                            guardrail=G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER,
                            code="NUMERIC_BOUNDS",
                            message=f"Field '{field}' must be between {minimum} and {maximum}.",
                        )
                    )

        return PolicyResult(
            allowed=not violations,
            sanitized=sanitized,
            violations=violations,
        )


def check_ai_enabled(kill_switch: bool) -> PolicyResult:
    violations = []
    if kill_switch:
        from atmpl.guardrails.constants import G6_KILL_SWITCH

        violations.append(
            PolicyViolation(
                guardrail=G6_KILL_SWITCH,
                code="KILL_SWITCH_ACTIVE",
                message="Organization kill switch is active; AI execution is parked.",
            )
        )
    return PolicyResult(allowed=not violations, sanitized={}, violations=violations)
