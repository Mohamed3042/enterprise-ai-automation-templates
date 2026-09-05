"""Write the served `/api/v1` OpenAPI document to docs/openapi.v1.json.

The snapshot is a committed contract: `tests/test_api_contract.py` fails when the served
schema drifts from it, so an accidental field rename cannot ship unnoticed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from atmpl.api import create_api
from atmpl.engine.database import Database
from atmpl.engine.service import AutomationEngine
from atmpl.runtime import build_context
from atmpl.settings import Settings

SNAPSHOT = Path(__file__).resolve().parents[1] / "docs" / "openapi.v1.json"


def build_document() -> dict:
    """Build the API in memory; the schema must not depend on any stored state."""
    with tempfile.TemporaryDirectory() as directory:
        audit_path = Path(directory) / "audit.jsonl"
        engine = AutomationEngine(Database("sqlite:///:memory:"), audit_path)
        context = build_context(engine, Settings(_env_file=None))
        document = create_api(context).openapi()
    return document


def main() -> int:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        json.dumps(build_document(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"WROTE: {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
