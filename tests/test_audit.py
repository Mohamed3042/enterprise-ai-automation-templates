import json
from pathlib import Path

from atmpl.audit import GENESIS_HASH, AuditLog, verify_audit


def test_audit_chain_verifies_and_links_records(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    first = log.append("created", actor="system", payload={"synthetic": True})
    second = log.append("reviewed", actor="Synthetic Reviewer", payload={"decision": "approve"})

    verification = verify_audit(path)
    assert verification.valid
    assert verification.count == 2
    assert first["prev_hash"] == GENESIS_HASH
    assert second["prev_hash"] == first["hash"]
    assert verification.last_hash == second["hash"]


def test_audit_tampering_fails_loudly(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).append("created", actor="system", payload={"synthetic": True})
    record = json.loads(path.read_text(encoding="utf-8"))
    record["actor"] = "forged"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    verification = verify_audit(path)
    assert not verification.valid
    assert "hash mismatch" in (verification.error or "").lower()


def test_missing_audit_file_fails_closed(tmp_path: Path):
    verification = verify_audit(tmp_path / "missing.jsonl")

    assert not verification.valid
    assert "does not exist" in (verification.error or "")

