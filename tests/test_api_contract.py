"""Contract tests for `/api/v1`: every route, happy path and error path."""

import json

import yaml

from atmpl.catalog import PROJECT_ROOT
from atmpl.demos import DEMO_INBOUND_SOURCES  # noqa: F401  (imported for the source list below)

SNAPSHOT = PROJECT_ROOT / "docs" / "openapi.v1.json"


def _valid_retail_answers() -> dict:
    raw = (PROJECT_ROOT / "demos" / "retail" / "eu.answers.yaml").read_text(encoding="utf-8")
    answers = yaml.safe_load(raw)
    answers["organization"]["name"] = "Synthetic API Org"
    return answers


# --------------------------------------------------------------------------- ops


def test_health_and_ready(client):
    health = client.get("/api/v1/health")
    ready = client.get("/api/v1/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": "0.2.0", "adapter": "mock"}
    assert ready.status_code == 200
    assert ready.json()["database"] == "reachable"


def test_request_id_is_echoed_or_minted(client):
    echoed = client.get("/api/v1/health", headers={"X-Request-Id": "syn-req-42"})
    minted = client.get("/api/v1/health")

    assert echoed.headers["X-Request-Id"] == "syn-req-42"
    assert len(minted.headers["X-Request-Id"]) == 32


def test_security_headers_are_present(client):
    response = client.get("/")

    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]


# --------------------------------------------------------------------------- templates


def test_list_and_read_templates(client):
    listing = client.get("/api/v1/templates")
    detail = client.get("/api/v1/templates/bank")

    assert listing.status_code == 200
    assert {item["key"] for item in listing.json()} == {
        "retail_refund",
        "lesson_preparation",
        "loan_triage",
    }
    body = detail.json()
    assert body["key"] == "loan_triage"
    assert "bank" in body["aliases"]
    assert any(stage["risk_tier"] == "HIGH" for stage in body["stages"])
    assert body["placeholder_schema"]["type"] == "object"


def test_unknown_template_uses_the_error_envelope(client):
    response = client.get("/api/v1/templates/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "template_not_found"
    assert "does-not-exist" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- discovery


def test_discovery_round_trip_fails_closed_then_compiles(client):
    created = client.post(
        "/api/v1/discovery/sessions",
        json={"template": "retail", "organization": "Synthetic API Org"},
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    assert created.json()["template"] == "retail_refund"
    assert len(created.json()["questions"]) >= 10

    blank = client.put(
        f"/api/v1/discovery/sessions/{session_id}/answers",
        json={"answers": created.json()["answers"]},
    )
    assert blank.status_code == 200
    assert blank.json()["valid"] is False
    assert blank.json()["follow_ups"]

    refused = client.post(f"/api/v1/discovery/sessions/{session_id}/resolve")
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "discovery_incomplete"

    filled = client.put(
        f"/api/v1/discovery/sessions/{session_id}/answers",
        json={"answers": _valid_retail_answers()},
    )
    assert filled.json() == {"id": session_id, "valid": True, "follow_ups": []}

    resolved = client.post(f"/api/v1/discovery/sessions/{session_id}/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["stage_count"] == 5
    assert resolved.json()["workflow_id"].startswith("wf_synthetic-api-org")


def test_cross_boundary_policy_pack_is_a_follow_up_not_a_compile(client):
    created = client.post(
        "/api/v1/discovery/sessions",
        json={"template": "retail", "organization": "Synthetic Boundary Org"},
    )
    answers = _valid_retail_answers()
    answers["regional"]["policy_pack"] = "us_standard"
    response = client.put(
        f"/api/v1/discovery/sessions/{created.json()['id']}/answers",
        json={"answers": answers},
    )

    assert response.json()["valid"] is False
    assert response.json()["follow_ups"][0]["placeholder"] == "regional.policy_pack"


def test_unknown_discovery_session_is_404(client):
    response = client.get("/api/v1/discovery/sessions/dsc_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- runs


def test_start_run_read_it_back_and_page_the_list(client):
    created = client.post(
        "/api/v1/runs",
        json={
            "workflow_id": "wf_bank_triage",
            "title": "SYN-LOAN-7000 — started over the API",
            "evidence": {"channel": "api-contract-test"},
        },
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["stages"][0]["evidence"] == {"channel": "api-contract-test"}

    detail = client.get(f"/api/v1/runs/{run_id}")
    stages = client.get(f"/api/v1/runs/{run_id}/stages")
    assert detail.json()["organization_name"] == "Gulf Horizon Bank"
    assert [stage["stage_key"] for stage in stages.json()] == [
        stage["stage_key"] for stage in detail.json()["stages"]
    ]

    first = client.get("/api/v1/runs", params={"limit": 2}).json()
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    second = client.get("/api/v1/runs", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]} == set()


def test_run_creation_requires_exactly_one_source(client):
    response = client.post("/api/v1/runs", json={"title": "Synthetic ambiguous run"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_workflow_and_unknown_run_are_404(client):
    missing_workflow = client.post(
        "/api/v1/runs",
        json={"workflow_id": "wf_missing", "title": "Synthetic missing workflow"},
    )
    missing_run = client.get("/api/v1/runs/run_missing")

    assert missing_workflow.status_code == 404
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["message"] == "Run not found: run_missing"


def test_invalid_cursor_is_rejected(client):
    response = client.get("/api/v1/runs", params={"cursor": "not-a-cursor"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"


def test_v1_decision_records_the_principal_in_the_ledger(client):
    response = client.post(
        "/api/v1/runs/run_bank_pending/stages/terminal_decision/decision",
        json={
            "actor": "Synthetic API Reviewer",
            "role": "credit_officer_tier_2",
            "decision": "reject",
            "reason": "Synthetic evidence does not meet the human credit standard.",
            "signature": "sig_api_reviewer_001",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["principal"] == "human:demo"

    entries = client.get(
        "/api/v1/audit/entries", params={"event_type": "human_decision_recorded"}
    ).json()
    assert entries["items"][0]["payload"]["principal"] == "human:demo"


def test_guardrail_rejection_uses_the_error_envelope(client):
    response = client.post(
        "/api/v1/runs/run_bank_pending/stages/terminal_decision/decision",
        json={
            "actor": "Synthetic Intern",
            "role": "synthetic_intern",
            "decision": "approve",
            "reason": "Synthetic out-of-matrix attempt.",
            "signature": "sig_intern_001",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "guardrail_blocked"
    assert response.json()["error"]["details"]["guardrail"] == "G4"


# --------------------------------------------------------------------------- audit


def test_audit_entries_page_and_verify(client):
    page = client.get("/api/v1/audit/entries", params={"limit": 3}).json()
    verification = client.post("/api/v1/audit/verify").json()

    assert len(page["items"]) == 3
    assert page["items"][0]["sequence"] > page["items"][1]["sequence"]
    assert verification["valid"] is True
    assert verification["count"] > 0


# --------------------------------------------------------------------------- OpenAPI


def test_openapi_document_is_served_with_docs(client):
    schema = client.get("/api/v1/openapi.json")
    docs = client.get("/api/v1/docs")

    assert schema.status_code == 200
    assert schema.json()["info"]["version"] == "0.2.0"
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()


def test_served_openapi_matches_the_committed_snapshot(client):
    served = client.get("/api/v1/openapi.json").json()
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert served == committed, (
        "The served OpenAPI document drifted from docs/openapi.v1.json. "
        "Run: python scripts/export_openapi.py"
    )


def test_every_documented_path_is_reachable(client):
    served = client.get("/api/v1/openapi.json").json()
    expected = {
        "/health",
        "/ready",
        "/oauth/token",
        "/templates",
        "/templates/{key}",
        "/discovery/sessions",
        "/discovery/sessions/{session_id}",
        "/discovery/sessions/{session_id}/answers",
        "/discovery/sessions/{session_id}/resolve",
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/stages",
        "/runs/{run_id}/stages/{stage_key}/decision",
        "/audit/entries",
        "/audit/verify",
        "/webhooks/subscriptions",
        "/webhooks/subscriptions/{subscription_id}",
        "/webhooks/deliveries",
        "/webhooks/deliveries/{delivery_id}/retry",
        "/webhooks/{source}",
    }

    assert set(served["paths"]) == expected
