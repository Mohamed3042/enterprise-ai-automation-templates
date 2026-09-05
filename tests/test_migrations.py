"""Alembic is the schema authority for a server deployment; SQLite demos still self-create."""

import os

import pytest
from sqlalchemy import create_engine, inspect

from atmpl.engine.database import Base, Database
from atmpl.migrate import current_revision, upgrade_database

EXPECTED_TABLES = {
    "api_keys",
    "audit_events",
    "discovery_sessions",
    "inbound_events",
    "oauth_clients",
    "organizations",
    "outbox_events",
    "redteam_results",
    "runs",
    "stages",
    "webhook_deliveries",
    "webhook_subscriptions",
    "workflows",
}


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


@pytest.fixture
def fresh_url(tmp_path) -> str:
    url = os.environ.get("ATMPL_TEST_DATABASE_URL")
    if url:
        engine = create_engine(url)
        try:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        finally:
            engine.dispose()
        return url
    return f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"


def test_upgrade_creates_the_whole_schema_and_records_the_revision(fresh_url):
    assert current_revision(fresh_url) is None

    revision = upgrade_database(fresh_url)

    assert revision == "0001"
    assert _tables(fresh_url) >= EXPECTED_TABLES
    assert current_revision(fresh_url) == "0001"


def test_upgrade_is_idempotent(fresh_url):
    upgrade_database(fresh_url)

    assert upgrade_database(fresh_url) == "0001"


def test_a_database_built_by_create_all_is_baseline_stamped_not_re_ddl_ed(fresh_url):
    """The keyless demo creates its tables in-process; upgrading must not try to re-create them."""
    Database(fresh_url, create_all=True)
    assert current_revision(fresh_url) is None

    assert upgrade_database(fresh_url) == "0001"
    assert _tables(fresh_url) >= EXPECTED_TABLES


def test_the_orm_metadata_and_the_migration_agree(fresh_url):
    """A model added without a migration would show up here as a missing table."""
    upgrade_database(fresh_url)
    migrated = _tables(fresh_url)

    assert set(Base.metadata.tables) <= migrated
