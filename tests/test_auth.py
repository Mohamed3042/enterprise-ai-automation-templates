"""Authentication, scopes, sessions, and the hardening middleware."""

import json

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, auth
from sqlalchemy import select

from atmpl.engine.database import ApiKey, AuditEvent
from atmpl.security.credentials import (
    CredentialError,
    create_api_key,
    create_oauth_client,
    hash_password,
    verify_password,
)
from atmpl.security.dependencies import SESSION_COOKIE
from atmpl.security.tokens import mint_access_token
from atmpl.settings import Settings
from atmpl.web.app import create_app

DECISION = {
    "actor": "Synthetic API Reviewer",
    "role": "credit_officer_tier_2",
    "decision": "reject",
    "reason": "Synthetic evidence does not meet the human credit standard.",
    "signature": "sig_api_reviewer_001",
}


# --------------------------------------------------------------------------- passwords


def test_password_hash_is_pbkdf2_and_verifies_only_the_right_password():
    encoded = hash_password("synthetic-demo-password")

    assert encoded.startswith("pbkdf2_sha256$600000$")
    assert "synthetic-demo-password" not in encoded
    assert verify_password("synthetic-demo-password", encoded)
    assert not verify_password("synthetic-demo-passwore", encoded)
    assert not verify_password("synthetic-demo-password", None)


# --------------------------------------------------------------------------- API keys


def test_api_key_plaintext_is_never_stored(seeded_engine):
    with seeded_engine.database.session() as session:
        issued = create_api_key(session, name="synthetic-erp", scopes="runs:read")
        stored = session.scalar(select(ApiKey).where(ApiKey.name == "synthetic-erp"))

    assert issued.token.startswith("atmpl_")
    assert issued.token not in json.dumps(
        {"salt": stored.salt, "hash": stored.secret_hash, "prefix": stored.prefix}
    )
    assert stored.secret_hash != issued.token


def test_unknown_scope_is_refused_at_creation(seeded_engine):
    with seeded_engine.database.session() as session, pytest.raises(ValueError, match="Unknown"):
        create_api_key(session, name="synthetic-bad", scopes="runs:read,worlds:destroy")


def test_duplicate_key_name_is_refused(seeded_engine):
    with seeded_engine.database.session() as session:
        create_api_key(session, name="synthetic-dup", scopes="runs:read")
        with pytest.raises(CredentialError):
            create_api_key(session, name="synthetic-dup", scopes="runs:read")


def test_api_key_authenticates_and_records_last_use(locked_client, seeded_engine, make_key):
    token = make_key("synthetic-reader", "runs:read")
    response = locked_client.get("/api/v1/runs", headers=auth(token))

    assert response.status_code == 200
    with seeded_engine.database.session() as session:
        stored = session.scalar(select(ApiKey).where(ApiKey.name == "synthetic-reader"))
    assert stored.last_used_at is not None


def test_scope_denial_is_403_and_names_what_is_missing(locked_client, make_key):
    token = make_key("synthetic-readonly", "runs:read")
    response = locked_client.post(
        "/api/v1/runs/run_bank_pending/stages/terminal_decision/decision",
        json=DECISION,
        headers=auth(token),
    )

    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "insufficient_scope"
    assert body["details"]["required"] == "decisions:write"
    assert body["details"]["granted"] == ["runs:read"]


def test_revoked_key_stops_working(locked_client, seeded_engine, make_key):
    from atmpl.security.credentials import revoke_api_key

    token = make_key("synthetic-revoked", "runs:read")
    assert locked_client.get("/api/v1/runs", headers=auth(token)).status_code == 200
    with seeded_engine.database.session() as session:
        assert revoke_api_key(session, "synthetic-revoked")

    assert locked_client.get("/api/v1/runs", headers=auth(token)).status_code == 401


def test_missing_credential_is_401_on_a_configured_deployment(locked_client):
    response = locked_client.get("/api/v1/runs")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_keyless_demo_opens_the_api_only_with_the_explicit_switch(seeded_engine, settings_factory):
    closed = create_app(seeded_engine, settings_factory(demo_open_api=False))
    from fastapi.testclient import TestClient

    with TestClient(closed) as client:
        assert client.get("/api/v1/runs").status_code == 401
        assert client.get("/").status_code == 200


# --------------------------------------------------------------------------- OAuth2


def test_client_credentials_grant_returns_a_scoped_token(locked_client, seeded_engine):
    with seeded_engine.database.session() as session:
        client = create_oauth_client(
            session, name="Synthetic ERP", scopes="runs:read,decisions:write"
        )

    token_response = locked_client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "scope": "runs:read",
        },
    )
    assert token_response.status_code == 200
    body = token_response.json()
    assert body["token_type"] == "Bearer"
    assert body["scope"] == "runs:read"
    assert 0 < body["expires_in"] <= 900

    allowed = locked_client.get("/api/v1/runs", headers=auth(body["access_token"]))
    denied = locked_client.post(
        "/api/v1/runs/run_bank_pending/stages/terminal_decision/decision",
        json=DECISION,
        headers=auth(body["access_token"]),
    )
    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_wrong_secret_and_unsupported_grant_are_refused(locked_client, seeded_engine):
    with seeded_engine.database.session() as session:
        client = create_oauth_client(session, name="Synthetic CRM", scopes="runs:read")

    wrong = locked_client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client.client_id,
            "client_secret": "not-the-secret",
        },
    )
    grant = locked_client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "password",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )

    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_client"
    assert grant.status_code == 400
    assert grant.json()["error"]["code"] == "unsupported_grant_type"


def test_scope_beyond_the_grant_is_refused(locked_client, seeded_engine):
    with seeded_engine.database.session() as session:
        client = create_oauth_client(session, name="Synthetic Narrow", scopes="runs:read")

    response = locked_client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "scope": "runs:read decisions:write",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_scope"


def test_expired_token_is_401(locked_client):
    context = locked_client.app.state.context
    token, _ = mint_access_token(
        subject="cli_expired",
        scopes=["runs:read"],
        key=context.jwt_key,
        key_id=context.settings.jwt_key_id,
        ttl_seconds=-10,
    )

    response = locked_client.get("/api/v1/runs", headers=auth(token))

    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"].lower()


def test_token_signed_with_an_unknown_kid_is_401(locked_client):
    context = locked_client.app.state.context
    token, _ = mint_access_token(
        subject="cli_rotated",
        scopes=["runs:read"],
        key=context.jwt_key,
        key_id="atmpl-hs256-retired",
        ttl_seconds=300,
    )

    response = locked_client.get("/api/v1/runs", headers=auth(token))

    assert response.status_code == 401
    assert "atmpl-hs256-retired" in response.json()["error"]["message"]


def test_token_signed_with_another_key_is_401(locked_client):
    context = locked_client.app.state.context
    token, _ = mint_access_token(
        subject="cli_forged",
        scopes=["runs:read"],
        key="an-attacker-chosen-key-long-enough-for-hs256",
        key_id=context.settings.jwt_key_id,
        ttl_seconds=300,
    )

    assert locked_client.get("/api/v1/runs", headers=auth(token)).status_code == 401


# --------------------------------------------------------------------------- dashboard


def test_dashboard_requires_a_login_and_then_grants_one(locked_client):
    guarded = locked_client.get("/approvals", follow_redirects=False)
    rejected = locked_client.post(
        "/login", data={"username": ADMIN_USER, "password": "wrong"}, follow_redirects=False
    )
    accepted = locked_client.post(
        "/login",
        data={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )

    assert guarded.status_code == 303
    assert guarded.headers["location"] == "/login"
    assert rejected.status_code == 401
    assert accepted.status_code == 303
    cookie = accepted.cookies[SESSION_COOKIE]
    assert cookie
    assert locked_client.get("/approvals").status_code == 200


def test_login_attempts_reach_the_ledger(locked_client, seeded_engine):
    locked_client.post("/login", data={"username": ADMIN_USER, "password": "wrong"})

    with seeded_engine.database.session() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "dashboard_login_failed")
            .order_by(AuditEvent.sequence.desc())
        )
    assert event.actor == f"human:{ADMIN_USER}"
    assert event.payload["outcome"] == "rejected"


def test_tampered_session_cookie_is_rejected(locked_client):
    locked_client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    good = locked_client.cookies[SESSION_COOKIE]
    body, _, signature = good.partition(".")
    forged = f"{body}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"

    response = locked_client.get(
        "/approvals", cookies={SESSION_COOKIE: forged}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_form_decision_without_the_csrf_token_is_403(locked_client):
    locked_client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    page = locked_client.get("/approvals")
    assert 'name="csrf_token"' in page.text

    response = locked_client.post(
        "/api/runs/run_bank_pending/stages/terminal_decision/decision",
        data={**DECISION},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_form_decision_with_the_csrf_token_is_accepted(locked_client):
    locked_client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    page = locked_client.get("/approvals")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]

    response = locked_client.post(
        "/api/runs/run_bank_pending/stages/terminal_decision/decision",
        data={**DECISION, "csrf_token": token},
    )

    assert response.status_code == 200
    assert "Signed human decision recorded" in response.text


def test_the_audit_page_shows_who_was_signed_in(locked_client, seeded_engine):
    locked_client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    page = locked_client.get("/approvals")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    locked_client.post(
        "/api/runs/run_bank_pending/stages/terminal_decision/decision",
        data={**DECISION, "csrf_token": token},
    )

    audit_page = locked_client.get("/audit")
    run_page = locked_client.get("/runs/run_bank_pending")

    assert f"human:{ADMIN_USER}" in audit_page.text
    assert f"human:{ADMIN_USER}" in run_page.text
    with seeded_engine.database.session() as session:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "human_decision_recorded")
            .order_by(AuditEvent.sequence.desc())
        )
    assert event.payload["principal"] == f"human:{ADMIN_USER}"


# --------------------------------------------------------------------------- hardening


def test_rate_limit_returns_429_with_retry_after(seeded_engine, settings_factory):
    from fastapi.testclient import TestClient

    app = create_app(seeded_engine, settings_factory(rate_limit="3/minute", demo_open_api=True))
    with TestClient(app) as client:
        codes = [client.get("/api/v1/runs").status_code for _ in range(5)]

    assert codes[:3] == [200, 200, 200]
    assert codes[-1] == 429


def test_oversized_body_is_refused_before_parsing(seeded_engine, settings_factory):
    from fastapi.testclient import TestClient

    app = create_app(seeded_engine, settings_factory(max_body_bytes=256, demo_open_api=True))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/runs",
            json={"workflow_id": "wf_bank_triage", "title": "x" * 4096},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_cors_allowlist_is_off_until_configured(seeded_engine, settings_factory):
    from fastapi.testclient import TestClient

    closed = TestClient(create_app(seeded_engine, settings_factory(demo_open_api=True)))
    opened = TestClient(
        create_app(
            seeded_engine,
            settings_factory(demo_open_api=True, allowed_origins=["https://synthetic.example"]),
        )
    )
    headers = {"Origin": "https://synthetic.example"}

    closed_headers = closed.get("/api/v1/health", headers=headers).headers
    assert "access-control-allow-origin" not in closed_headers
    assert (
        opened.get("/api/v1/health", headers=headers).headers["access-control-allow-origin"]
        == "https://synthetic.example"
    )


def test_hsts_only_appears_when_it_is_switched_on(seeded_engine, settings_factory):
    from fastapi.testclient import TestClient

    plain = TestClient(create_app(seeded_engine, settings_factory()))
    secured = TestClient(create_app(seeded_engine, settings_factory(hsts_enabled=True)))

    assert "Strict-Transport-Security" not in plain.get("/").headers
    assert secured.get("/").headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_settings_reject_a_nonsense_rate_limit_and_backend():
    with pytest.raises(ValueError, match="ATMPL_RATE_LIMIT"):
        Settings(_env_file=None, rate_limit="lots/minute").rate_limit_parts()
    with pytest.raises(ValueError, match="Unknown secrets backend"):
        Settings(_env_file=None, secrets_backend="vault")
