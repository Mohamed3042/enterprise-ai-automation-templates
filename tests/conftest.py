import os
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atmpl.demos import seed_demo_data
from atmpl.engine.service import AutomationEngine
from atmpl.security.credentials import create_api_key, hash_password
from atmpl.settings import Settings
from atmpl.web.app import create_app

ADMIN_USER = "synthetic.admin"
ADMIN_PASSWORD = "synthetic-demo-password"


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch):
    """A developer's own ATMPL_* variables must not decide what the tests measure."""
    for name in [key for key in list(os.environ) if key.startswith("ATMPL_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def seeded_engine(tmp_path: Path) -> AutomationEngine:
    return seed_demo_data(tmp_path / "demo.db", tmp_path / "audit.jsonl")


@pytest.fixture
def settings_factory(tmp_path: Path) -> Callable[..., Settings]:
    def build(**overrides) -> Settings:
        defaults = {
            "_env_file": None,
            "database_url": f"sqlite:///{(tmp_path / 'settings.db').as_posix()}",
            "rate_limit": "0/minute",
        }
        return Settings(**{**defaults, **overrides})

    return build


@pytest.fixture
def client(seeded_engine, settings_factory) -> TestClient:
    """The keyless demo: no admin configured, dashboard open, API credential-free."""
    return TestClient(create_app(seeded_engine, settings_factory(demo_open_api=True)))


@pytest.fixture
def locked_client(seeded_engine, settings_factory) -> TestClient:
    """A configured deployment: dashboard needs a login, API needs a credential."""
    settings = settings_factory(
        admin_user=ADMIN_USER,
        admin_password_hash=hash_password(ADMIN_PASSWORD),
    )
    return TestClient(create_app(seeded_engine, settings))


@pytest.fixture
def make_key(seeded_engine) -> Callable[..., str]:
    def build(name: str, scopes: str) -> str:
        with seeded_engine.database.session() as session:
            return create_api_key(session, name=name, scopes=scopes).token

    return build


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
