"""Append-only JSONL audit ledger mirrored into SQLite."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

GENESIS_HASH = "0" * 64


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class AuditVerification:
    valid: bool
    count: int
    last_hash: str
    error: str | None = None


class AuditLog:
    def __init__(
        self,
        path: Path,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_factory = session_factory
        self._lock = threading.Lock()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS_HASH
        lines = self.path.read_text(encoding="utf-8").splitlines()
        last = json.loads(lines[-1])
        return int(last["sequence"]), str(last["hash"])

    def append(
        self,
        event_type: str,
        *,
        actor: str,
        payload: dict[str, Any],
        organization_id: str | None = None,
        run_id: str | None = None,
        stage_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            sequence, prev_hash = self._tail()
            record = {
                "event_id": f"evt_{uuid4().hex}",
                "sequence": sequence + 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "event_type": event_type,
                "organization_id": organization_id,
                "run_id": run_id,
                "stage_id": stage_id,
                "actor": actor,
                "payload": payload,
                "prev_hash": prev_hash,
            }
            record_hash = hashlib.sha256(_canonical(record)).hexdigest()
            persisted = {**record, "hash": record_hash}
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(persisted, ensure_ascii=False, sort_keys=True) + "\n")

            if self.session_factory:
                from atmpl.engine.database import AuditEvent

                with self.session_factory() as session:
                    session.add(
                        AuditEvent(
                            event_id=record["event_id"],
                            sequence=record["sequence"],
                            event_type=event_type,
                            organization_id=organization_id,
                            run_id=run_id,
                            stage_id=stage_id,
                            actor=actor,
                            payload=payload,
                            prev_hash=prev_hash,
                            record_hash=record_hash,
                            timestamp=datetime.fromisoformat(record["timestamp"]),
                        )
                    )
                    session.commit()
            return persisted


def verify_audit(path: Path) -> AuditVerification:
    if not path.exists():
        return AuditVerification(False, 0, GENESIS_HASH, f"Audit file does not exist: {path}")

    previous = GENESIS_HASH
    count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            persisted = json.loads(line)
        except json.JSONDecodeError as exc:
            return AuditVerification(
                False,
                count,
                previous,
                f"Line {line_number}: invalid JSON: {exc.msg}",
            )
        claimed_hash = persisted.pop("hash", None)
        if persisted.get("prev_hash") != previous:
            return AuditVerification(
                False,
                count,
                previous,
                f"Line {line_number}: previous-hash link mismatch.",
            )
        actual_hash = hashlib.sha256(_canonical(persisted)).hexdigest()
        if claimed_hash != actual_hash:
            return AuditVerification(
                False,
                count,
                previous,
                f"Line {line_number}: SHA-256 record hash mismatch.",
            )
        previous = actual_hash
        count += 1
    return AuditVerification(True, count, previous)
