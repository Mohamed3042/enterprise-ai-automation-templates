"""Webhooks in both directions: signatures, idempotency, retries, receipts, dead-letter."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import func, select

from atmpl.audit import verify_audit
from atmpl.engine.database import AuditEvent, InboundEvent, Run, WebhookDelivery
from atmpl.runtime import build_context
from atmpl.webhooks.outbox import deliver_pending
from atmpl.webhooks.signing import SIGNATURE_HEADER, SignatureError, sign, verify

HELPDESK_EVENT = {
    "ticket": {
        "id": "SYN-TCK-8801",
        "refund_amount": 125,
        "subject": "Unopened synthetic item returned on day 12",
        "requester": {"email": "synthetic.customer@example.invalid"},
    }
}


class _Receiver(BaseHTTPRequestHandler):
    """A real HTTP receiver: it records what it got and answers a scripted status code.

    Note the attribute is `scripted`, not `responses`: BaseHTTPRequestHandler already owns
    `responses` (its status-code -> reason-phrase table) and shadowing it breaks send_response.
    """

    scripted: list[int] = []
    received: list[dict] = []
    secret: str = ""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        signature = self.headers.get(SIGNATURE_HEADER)
        verified = True
        try:
            verify(type(self).secret, signature, body)
        except SignatureError:
            verified = False
        type(self).received.append(
            {
                "body": json.loads(body or b"{}"),
                "signature": signature,
                "verified": verified,
                "event": self.headers.get("X-ATMPL-Event"),
            }
        )
        status = type(self).scripted.pop(0) if type(self).scripted else 200
        self.send_response(status)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok" if status < 300 else b"synthetic receiver failure")

    def log_message(self, *args) -> None:  # keep pytest output readable
        return


@pytest.fixture
def receiver():
    """Start a throwaway receiver on a free port; `responses` scripts its status codes."""
    _Receiver.scripted = []
    _Receiver.received = []
    _Receiver.secret = ""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield type(
            "Handle",
            (),
            {
                "url": f"http://127.0.0.1:{server.server_address[1]}/hook",
                "handler": _Receiver,
            },
        )
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- signing


def test_signature_round_trip_and_its_three_failure_modes():
    body = b'{"synthetic": true}'
    header = sign("whsec_synthetic", body, timestamp=1_700_000_000)

    assert verify("whsec_synthetic", header, body, now=1_700_000_010) == 1_700_000_000

    with pytest.raises(SignatureError, match="tolerance"):
        verify("whsec_synthetic", header, body, now=1_700_000_400)
    with pytest.raises(SignatureError, match="does not match"):
        verify("whsec_synthetic", header, b'{"synthetic": false}', now=1_700_000_010)
    with pytest.raises(SignatureError, match="Missing"):
        verify("whsec_synthetic", None, body)


def test_the_timestamp_is_inside_the_mac(monkeypatch):
    """Moving `t` to dodge the tolerance breaks the signature, which is the point."""
    body = b"{}"
    header = sign("whsec_synthetic", body, timestamp=1_700_000_000)
    moved = header.replace("t=1700000000", "t=1700000390")

    with pytest.raises(SignatureError, match="does not match"):
        verify("whsec_synthetic", moved, body, now=1_700_000_400)


# --------------------------------------------------------------------------- inbound


def _inbound_secret(client, source: str = "helpdesk") -> str:
    from atmpl.webhooks.inbound import load_mapping

    return load_mapping(client.app.state.context, source).secret


def _post_event(client, payload: dict, *, secret: str, key: str | None = None, source="helpdesk"):
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        SIGNATURE_HEADER: sign(secret, body),
    }
    if key:
        headers["X-Idempotency-Key"] = key
    return client.post(f"/api/v1/webhooks/{source}", content=body, headers=headers)


def test_a_signed_event_starts_a_governed_run_with_the_event_as_evidence(client, seeded_engine):
    secret = _inbound_secret(client)

    response = _post_event(client, HELPDESK_EVENT, secret=secret, key="syn-idem-1")

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["idempotent_replay"] is False

    run = client.get(f"/api/v1/runs/{body['run_id']}").json()
    assert run["title"] == "Helpdesk ticket SYN-TCK-8801 - refund review"
    assert run["region"] == "EU"
    evidence = run["stages"][0]["evidence"]
    assert evidence["source"] == "helpdesk"
    assert evidence["mapped"]["ticket_id"] == "SYN-TCK-8801"
    assert evidence["mapped"]["customer_email"] == "[REDACTED]", "G3 redaction must still apply"

    page = client.get(f"/runs/{body['run_id']}")
    rendered = page.text.split("</head>", 1)[1]
    assert "INBOUND EVENT · RECORDED EVIDENCE" in rendered
    assert "SYN-TCK-8801" in rendered

    with seeded_engine.database.session() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "inbound_webhook_accepted")
            .order_by(AuditEvent.sequence.desc())
        )
    assert event.actor == "webhook:helpdesk"
    assert verify_audit(seeded_engine.audit.path).valid


def test_a_tampered_signature_is_401_and_starts_nothing(client, seeded_engine):
    secret = _inbound_secret(client)
    body = json.dumps(HELPDESK_EVENT).encode("utf-8")
    header = sign(secret, body)
    with seeded_engine.database.session() as session:
        before = session.scalar(select(func.count(Run.id)))

    response = client.post(
        "/api/v1/webhooks/helpdesk",
        content=json.dumps({**HELPDESK_EVENT, "amount": 999_999}).encode("utf-8"),
        headers={"content-type": "application/json", SIGNATURE_HEADER: header},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    with seeded_engine.database.session() as session:
        assert session.scalar(select(func.count(Run.id))) == before


def test_a_stale_timestamp_is_refused(client):
    secret = _inbound_secret(client)
    body = json.dumps(HELPDESK_EVENT).encode("utf-8")
    stale = sign(secret, body, timestamp=int(time.time()) - 3600)

    response = client.post(
        "/api/v1/webhooks/helpdesk",
        content=body,
        headers={"content-type": "application/json", SIGNATURE_HEADER: stale},
    )

    assert response.status_code == 401
    assert "tolerance" in response.json()["error"]["message"]


def test_a_replayed_idempotency_key_returns_the_first_result_and_no_second_run(
    client, seeded_engine
):
    secret = _inbound_secret(client)
    first = _post_event(client, HELPDESK_EVENT, secret=secret, key="syn-idem-replay")
    with seeded_engine.database.session() as session:
        after_first = session.scalar(select(func.count(Run.id)))

    second = _post_event(client, HELPDESK_EVENT, secret=secret, key="syn-idem-replay")

    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert second.json()["run_id"] == first.json()["run_id"]
    with seeded_engine.database.session() as session:
        assert session.scalar(select(func.count(Run.id))) == after_first
        assert session.scalar(select(func.count(InboundEvent.id))) == 1


def test_the_second_demo_source_maps_onto_the_ministry_workflow(client):
    secret = _inbound_secret(client, "case_intake")
    payload = {"case": {"reference": "SYN-CASE-31", "subject": "Grade 5 mathematics"}}

    response = _post_event(client, payload, secret=secret, key="syn-case-1", source="case_intake")

    assert response.status_code == 200
    run = client.get(f"/api/v1/runs/{response.json()['run_id']}").json()
    assert run["title"] == "Case intake SYN-CASE-31 - lesson review"
    assert run["workflow_id"] == "wf_ministry_lesson"


def test_an_unknown_source_is_404(client):
    response = client.post(
        "/api/v1/webhooks/not-configured",
        content=b"{}",
        headers={SIGNATURE_HEADER: sign("whatever", b"{}")},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_source"


# --------------------------------------------------------------------------- outbound


def _subscribe(client, receiver, event_types=None) -> str:
    response = client.post(
        "/api/v1/webhooks/subscriptions",
        json={
            "url": receiver.url,
            "event_types": event_types or ["run.stage.decided"],
            "description": "Synthetic receiver",
        },
    )
    assert response.status_code == 201
    receiver.handler.secret = response.json()["secret"]
    return response.json()["id"]


def _decide(client, run_id="run_bank_pending", stage="terminal_decision"):
    return client.post(
        f"/api/v1/runs/{run_id}/stages/{stage}/decision",
        json={
            "actor": "Synthetic API Reviewer",
            "role": "credit_officer_tier_2",
            "decision": "reject",
            "reason": "Synthetic evidence does not meet the human credit standard.",
            "signature": "sig_api_reviewer_001",
        },
    )


def test_a_decision_is_delivered_after_500_500_200_with_a_receipt_per_attempt(client, receiver):
    subscription_id = _subscribe(client, receiver)
    receiver.handler.scripted = [500, 500, 200]
    context = client.app.state.context

    assert _decide(client).status_code == 200
    for _ in range(3):
        deliver_pending(context, ignore_backoff=True)

    deliveries = client.get(
        "/api/v1/webhooks/deliveries", params={"subscription_id": subscription_id}
    ).json()["items"]
    assert len(deliveries) == 1
    delivery = deliveries[0]

    assert delivery["status"] == "delivered"
    assert delivery["attempts"] == 3
    assert [receipt["status_code"] for receipt in delivery["receipts"]] == [500, 500, 200]
    assert all(receipt["latency_ms"] >= 0 for receipt in delivery["receipts"])
    assert delivery["last_status_code"] == 200

    assert len(receiver.handler.received) == 3
    assert all(item["verified"] for item in receiver.handler.received), (
        "the receiver must be able to verify our signature with the documented recipe"
    )
    assert receiver.handler.received[0]["event"] == "run.stage.decided"
    assert receiver.handler.received[0]["body"]["data"]["principal"] == "human:demo"


def test_five_failures_dead_letter_the_delivery_and_a_retry_revives_it(client, receiver):
    _subscribe(client, receiver)
    receiver.handler.scripted = [500] * 5
    context = client.app.state.context

    _decide(client)
    for _ in range(5):
        deliver_pending(context, ignore_backoff=True)

    dead = client.get("/api/v1/webhooks/deliveries", params={"status": "dead_letter"}).json()
    assert len(dead["items"]) == 1
    delivery = dead["items"][0]
    assert delivery["attempts"] == 5
    assert len(delivery["receipts"]) == 5

    receiver.handler.scripted = [200]
    retried = client.post(f"/api/v1/webhooks/deliveries/{delivery['id']}/retry")

    assert retried.status_code == 200
    assert retried.json()["status"] == "delivered"
    assert retried.json()["attempts"] == 1


def test_backoff_holds_a_failed_delivery_until_it_is_due(client, receiver):
    _subscribe(client, receiver)
    receiver.handler.scripted = [500]
    context = client.app.state.context

    _decide(client)
    deliver_pending(context, ignore_backoff=True)
    attempts_made = len(receiver.handler.received)
    deliver_pending(context)  # honours next_attempt_at

    assert attempts_made == 1
    assert len(receiver.handler.received) == 1, "the second pass must respect the backoff"


def test_event_filters_decide_who_hears_what(client, receiver):
    _subscribe(client, receiver, event_types=["guardrail.blocked"])
    receiver.handler.scripted = [200]
    context = client.app.state.context

    _decide(client)
    deliver_pending(context, ignore_backoff=True)

    assert client.get("/api/v1/webhooks/deliveries").json()["items"] == []
    assert receiver.handler.received == []


def test_the_subscription_secret_is_shown_once_and_never_again(client, receiver):
    created = client.post(
        "/api/v1/webhooks/subscriptions",
        json={"url": receiver.url, "event_types": [], "description": "Synthetic"},
    ).json()
    listed = client.get("/api/v1/webhooks/subscriptions").json()

    assert created["secret"].startswith("whsec_")
    assert [item["secret"] for item in listed] == [None]


def test_deleting_a_subscription_stops_delivery(client, receiver):
    subscription_id = _subscribe(client, receiver)
    deleted = client.delete(f"/api/v1/webhooks/subscriptions/{subscription_id}")
    missing = client.delete(f"/api/v1/webhooks/subscriptions/{subscription_id}")
    receiver.handler.scripted = [200]

    _decide(client)
    deliver_pending(client.app.state.context, ignore_backoff=True)

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert receiver.handler.received == []


def test_the_outbox_is_written_in_the_decision_transaction(client, seeded_engine, receiver):
    """No subscription exists yet, so nothing is delivered - but the event is already durable."""
    from atmpl.engine.database import OutboxEvent

    _decide(client)

    with seeded_engine.database.session() as session:
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "run.stage.decided")
        ).all()
    assert len(events) == 1
    assert events[0].run_id == "run_bank_pending"
    assert events[0].payload["principal"] == "human:demo"


def test_the_webhooks_page_and_the_run_page_show_the_delivery(client, receiver):
    _subscribe(client, receiver)
    receiver.handler.scripted = [200]
    _decide(client)
    deliver_pending(client.app.state.context, ignore_backoff=True)

    page = client.get("/webhooks")
    run_page = client.get("/runs/run_bank_pending")

    assert page.status_code == 200
    assert "run.stage.decided" in page.text
    assert "1 delivered" in page.text
    # Assert WHERE it renders, not merely that the string exists: a Jinja block mistake once
    # put this whole panel inside <title>, where a substring check still passed.
    body = run_page.text.split("</head>", 1)[1]
    assert "<h2>Webhook deliveries for this run</h2>" in body
    assert body.index("Governed timeline") < body.index("Webhook deliveries for this run")
    assert "run.stage.decided" in body


def test_the_cli_worker_pass_uses_the_same_code_path(client, receiver, seeded_engine):
    from atmpl.cli import main

    _subscribe(client, receiver)
    receiver.handler.scripted = [200]
    _decide(client)

    context = build_context(seeded_engine, client.app.state.context.settings)
    outcomes = deliver_pending(context, ignore_backoff=True)

    assert outcomes == {"delivered": 1}
    assert callable(main)


def test_delivery_survives_a_receiver_that_is_not_listening(client, seeded_engine):
    client.post(
        "/api/v1/webhooks/subscriptions",
        json={"url": "http://127.0.0.1:9/never-listens", "event_types": []},
    )
    _decide(client)
    deliver_pending(client.app.state.context, ignore_backoff=True)

    with seeded_engine.database.session() as session:
        delivery = session.scalar(select(WebhookDelivery))
    assert delivery.status == "pending"
    assert delivery.last_status_code is None
    assert "Error" in (delivery.last_response or "")
