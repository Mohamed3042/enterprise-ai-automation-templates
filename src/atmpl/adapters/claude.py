"""Optional Claude HTTP adapter; never selected without explicit config and key."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

from atmpl.models import LLMOutput


class ClaudeAdapter:
    name = "claude"
    model = "claude-sonnet-5"

    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "Claude adapter requires ANTHROPIC_API_KEY; "
                "use the offline mock adapter by default."
            )

    def generate(self, task: str, payload: dict[str, Any]) -> LLMOutput:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1024,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Return one JSON object containing only a draft, flags, "
                            "and a recommendation. "
                            "Never make or claim a final decision.\n"
                            f"Task: {task}\nSanitized input: {canonical}"
                        ),
                    }
                ],
            },
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        text = body["content"][0]["text"]
        content = json.loads(text)
        digest = hashlib.sha256(f"{task}:{canonical}".encode()).hexdigest()
        return LLMOutput(task=task, input_hash=digest, content=content, adapter=self.name)
