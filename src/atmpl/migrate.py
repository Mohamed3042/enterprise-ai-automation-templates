"""Programmatic Alembic access, so `atmpl db upgrade` and Compose share one code path."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BASELINE_TABLES = {"organizations", "runs", "stages", "audit_events"}


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def upgrade_database(database_url: str) -> str:
    """Apply pending migrations.

    A database created by an older `create_all` has the tables but no `alembic_version`;
    stamping it is the correct baseline, not re-running DDL that would fail.
    """
    config = alembic_config(database_url)
    if current_revision(database_url) is None:
        engine = create_engine(database_url)
        try:
            existing = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        if existing >= BASELINE_TABLES:
            command.stamp(config, "head")
            return current_revision(database_url) or "head"
    command.upgrade(config, "head")
    return current_revision(database_url) or "head"
