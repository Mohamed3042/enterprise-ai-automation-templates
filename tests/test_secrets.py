"""The secrets provider, and the gate that says no secret reaches the ledger or the logs."""

import json
import logging
import os
import stat

import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USER, auth
from fastapi.testclient import TestClient

from atmpl.secrets import (
    EnvSecrets,
    FileSecrets,
    SecretNotConfigured,
    SecretResolver,
    build_provider,
)
from atmpl.security.credentials import create_api_key, create_oauth_client, hash_password
from atmpl.settings import Settings
from atmpl.web.app import create_app

JWT_SECRET = "synthetic-jwt-signing-key-0123456789abcdef"
SESSION_SECRET = "synthetic-session-signing-key-abcdef0123456789"


def test_env_backend_reads_prefixed_variables(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ATMPL_SECRET_JWT_SIGNING_KEY", JWT_SECRET)
    provider = EnvSecrets()

    assert provider.get("jwt_signing_key") == JWT_SECRET
    assert provider.get("missing_key") is None
    assert "jwt_signing_key" in provider.keys()  # noqa: SIM118 - list, not a mapping


def test_file_backend_reads_a_json_object(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"JWT_SIGNING_KEY": JWT_SECRET}), encoding="utf-8")
    provider = FileSecrets(path)

    assert provider.get("jwt_signing_key") == JWT_SECRET
    assert provider.keys() == ["jwt_signing_key"]
    assert FileSecrets(tmp_path / "absent.json").get("anything") is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_file_backend_refuses_a_world_readable_file(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)

    with pytest.raises(SecretNotConfigured, match="0600"):
        FileSecrets(path).get("jwt_signing_key")


def test_file_backend_needs_a_path():
    with pytest.raises(SecretNotConfigured, match="ATMPL_SECRETS_FILE"):
        build_provider(Settings(_env_file=None, secrets_backend="file"))


def test_resolver_requires_configured_values_but_keeps_the_demo_running():
    resolver = SecretResolver(EnvSecrets())

    with pytest.raises(SecretNotConfigured, match="jwt_signing_key"):
        resolver.require("jwt_signing_key")
    ephemeral = resolver.get_or_ephemeral("jwt_signing_key")
    assert resolver.is_ephemeral("jwt_signing_key")
    assert len(ephemeral) == 64
    assert resolver.get_or_ephemeral("jwt_signing_key") == ephemeral


def test_configured_secrets_never_reach_the_ledger_or_the_logs(
    seeded_engine,
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Exercise every credential path, then grep the ledger and the logs for each secret."""
    monkeypatch.setenv("ATMPL_SECRET_JWT_SIGNING_KEY", JWT_SECRET)
    monkeypatch.setenv("ATMPL_SECRET_SESSION_SIGNING_KEY", SESSION_SECRET)
    settings = settings_factory(
        admin_user=ADMIN_USER,
        admin_password_hash=hash_password(ADMIN_PASSWORD),
    )
    with seeded_engine.database.session() as session:
        key = create_api_key(session, name="synthetic-leak-probe", scopes="runs:read")
        oauth = create_oauth_client(session, name="Synthetic Leak", scopes="runs:read")

    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(seeded_engine, settings)) as client:
        client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
        client.get("/api/v1/runs", headers=auth(key.token))
        token = client.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": oauth.client_id,
                "client_secret": oauth.client_secret,
            },
        ).json()["access_token"]
        client.get("/api/v1/runs", headers=auth(token))
        client.post(
            "/api/v1/runs",
            json={"workflow_id": "wf_bank_triage", "title": "Synthetic leak-probe run"},
            headers=auth(key.token),
        )
        audit_page = client.get("/audit").text

    ledger = seeded_engine.audit.path.read_text(encoding="utf-8")
    logs = "\n".join(record.getMessage() for record in caplog.records)
    haystack = ledger + logs + audit_page

    secrets_in_play = {
        "jwt signing key": JWT_SECRET,
        "session signing key": SESSION_SECRET,
        "api key": key.token,
        "oauth client secret": oauth.client_secret,
        "admin password": ADMIN_PASSWORD,
        "access token": token,
    }
    leaked = [name for name, value in secrets_in_play.items() if value in haystack]

    assert leaked == [], f"secret material reached the ledger, logs or audit page: {leaked}"
    assert '"actor": "human:' in ledger, "the gate must run against a ledger that was written"


def test_the_leak_gate_can_fail(seeded_engine):
    """A gate that cannot fail is decoration: plant the secret and watch the same check fail."""
    seeded_engine.audit.append(
        "planted_leak",
        actor="test",
        payload={"oops": JWT_SECRET},
    )
    ledger = seeded_engine.audit.path.read_text(encoding="utf-8")

    assert JWT_SECRET in ledger
