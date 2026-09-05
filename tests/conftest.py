import os
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atmpl.demos import seed_demo_data
from atmpl.engine.database import Base, Database
from atmpl.engine.service import AutomationEngine
from atmpl.security.credentials import create_api_key, hash_password
from atmpl.settings import Settings
from atmpl.web.app import create_app

ADMIN_USER = "synthetic.admin"
ADMIN_PASSWORD = "synthetic-demo-password"

#: CI sets this to run the whole suite a second time against a PostgreSQL service container.
TEST_DATABASE_URL_VAR = "ATMPL_TEST_DATABASE_URL"


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch):
    """A developer's own ATMPL_* variables must not decide what the tests measure."""
    for name in list(os.environ):
        if name.startswith("ATMPL_") and name != TEST_DATABASE_URL_VAR:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    """SQLite by default; PostgreSQL when ATMPL_TEST_DATABASE_URL is set (same tests, twice)."""
    url = os.environ.get(TEST_DATABASE_URL_VAR)
    if not url:
        return Database(path=tmp_path / "demo.db")
    instance = Database(url, create_all=False)
    Base.metadata.drop_all(instance.engine)
    Base.metadata.create_all(instance.engine)
    return instance


@pytest.fixture
def seeded_engine(tmp_path: Path, database: Database) -> AutomationEngine:
    return seed_demo_data(tmp_path / "demo.db", tmp_path / "audit.jsonl", database=database)


@pytest.fixture
def settings_factory(tmp_path: Path) -> Callable[..., Settings]:
    def build(**overrides) -> Settings:
        defaults = {
            "_env_file": None,
            "database_url": os.environ.get(
                TEST_DATABASE_URL_VAR,
                f"sqlite:///{(tmp_path / 'settings.db').as_posix()}",
            ),
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
