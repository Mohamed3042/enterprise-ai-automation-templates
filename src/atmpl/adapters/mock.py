"""Deterministic offline adapter used by every seeded demo and CI run."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from atmpl.models import LLMOutput


class MockAdapter:
    name = "mock"

    def generate(self, task: str, payload: dict[str, Any]) -> LLMOutput:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        input_hash = hashlib.sha256(f"{task}:{canonical}".encode()).hexdigest()
        task_lower = task.lower()
        if "loan" in task_lower or "credit" in task_lower:
            content = {
                "summary": "Synthetic application is complete enough for officer review.",
                "recommendation": "REFER_TO_TIER_2",
                "completeness_score": 86,
                "risk_flags": ["income_variance"],
            }
        elif "lesson" in task_lower or "curriculum" in task_lower:
            content = {
                "summary": "Draft enrichment aligned to the selected synthetic curriculum profile.",
                "recommendation": "DEPARTMENT_REVIEW",
                "alignment_score": 92,
                "bilingual": True,
            }
        else:
            content = {
                "summary": "Synthetic request classified for governed human review.",
                "recommendation": "REVIEW",
                "confidence": 0.88,
            }
        return LLMOutput(task=task, input_hash=input_hash, content=content, adapter=self.name)

