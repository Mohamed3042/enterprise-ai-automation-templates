# 0004 — SQLite stays the front door; PostgreSQL becomes a first-class target

Status: accepted · 2026-09-05

## Context

`python -m atmpl demo up` with no environment variables is this project's front door: clone,
one command, a working governed dashboard. That has to survive adding a real database. At the
same time, the schema now holds credentials, an outbox and delivery logs — things a server
should keep properly, and things a reviewer expects to see on PostgreSQL.

## Decision

- One setting, `ATMPL_DATABASE_URL`, defaults to `sqlite:///var/atmpl.db`. PostgreSQL is
  `postgresql+psycopg://…` with the optional `postgres` extra, and is what Compose and one CI
  job run.
- Alembic owns the schema for a server: `atmpl db upgrade`, with migration `0001` equal to the
  whole current schema.
- The keyless path still calls `create_all` in-process (`create_schema_on_start` is true for
  SQLite, false otherwise), so the demo needs no migration step to work.
- `upgrade` **baseline-stamps** a database that already has the tables but no
  `alembic_version` instead of re-running DDL that would fail. That is the case for any
  database the demo created first.
- The test suite runs twice in CI: once on SQLite, once against a `postgres:16` service
  container, driven by `ATMPL_TEST_DATABASE_URL` in `tests/conftest.py`.

## Alternatives considered

- *Postgres everywhere, including the demo.* It would cost the repository its best property:
  a recruiter with Python and no Docker can still see it work.
- *SQLite everywhere.* Keeps one path, and leaves the "databases" claim unearned and the outbox
  under a writer lock.

## Consequences

- Two schema paths exist. The migration test asserts that every table in the ORM metadata is
  present after `upgrade`, so a model added without a migration is caught rather than silently
  working on SQLite only.
- Cursor pagination uses a row-value comparison (`(created_at, id) < (…)`), which SQLite 3.15+
  and PostgreSQL both support. This was a deliberate choice over `OFFSET`, and it earned itself:
  the first implementation compared random ids and returned overlapping pages, which the
  contract test caught immediately.
- The hash-chained ledger remains a file beside the database. `docs/architecture.md` says so,
  and Compose gives it a named volume — a lesson learned by watching a container rebuild leave
  a populated database with no ledger.
