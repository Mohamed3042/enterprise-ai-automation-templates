from sqlalchemy import select

from atmpl.engine.database import AuditEvent


def test_dashboard_pages_render_live_seeded_data(client):
    expectations = {
        "/": "Ship governed automation",
        "/runs/run_bank_override": "HUMAN AUTHORITY · CAPTURED IN AUDIT",
        "/runs/run_ministry_ar_math": "خطة درس الرياضيات",
        "/approvals": "Pending approvals",
        "/audit": "CHAIN VERIFIED",
        "/redteam": "24 / 24 attacks blocked",
    }
    for path, expected in expectations.items():
        response = client.get(path)
        assert response.status_code == 200
        assert expected in response.text


def test_high_api_bypass_returns_4xx_and_audits(client, seeded_engine):
    response = client.post(
        "/api/runs/run_bank_pending/stages/terminal_decision/decision",
        json={"decision": "approve"},
    )

    assert response.status_code == 422
    assert response.json()["status"] == "BLOCKED"
    assert response.json()["guardrail"] == "G2"
    with seeded_engine.database.session() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "approval_payload_rejected")
            .order_by(AuditEvent.sequence.desc())
        )
    assert event.payload["guardrail"] == "G2"


def test_dashboard_and_json_use_same_decision_endpoint(client):
    response = client.post(
        "/api/runs/run_bank_pending/stages/terminal_decision/decision",
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


def test_inline_arabic_uses_direction_auto(client):
    response = client.get("/runs/run_ministry_ar_math")

    assert 'dir="auto"' in response.text
    assert "المعلمة التجريبية أمل" in response.text

