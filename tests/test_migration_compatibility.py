"""Regression coverage for upgrades from the published 0.2.1 PostgreSQL schema."""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parent.parent
MIGRATIONS = ROOT / "migrations"

# This is the exact 0.2.1 migration published before change_marker existed.
LEGACY_021_SCHEMA = """\
CREATE TABLE IF NOT EXISTS changes (
  ingest_id bigserial UNIQUE NOT NULL,
  source_id text PRIMARY KEY,
  company_ico text NOT NULL,
  changed_at timestamptz NOT NULL,
  title text NOT NULL,
  url text NOT NULL,
  discovered_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS changes_incremental_identity_idx ON changes(ingest_id);
CREATE TABLE IF NOT EXISTS outbox (
  id bigserial PRIMARY KEY,
  mode text NOT NULL CHECK (mode IN ('historical','incremental')),
  subject text NOT NULL,
  body text NOT NULL,
  source_count integer NOT NULL CHECK (source_count BETWEEN 1 AND 500),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','failed')),
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  provider_id text,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz
);
CREATE TABLE IF NOT EXISTS checkpoints (
  name text PRIMARY KEY,
  value timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS monitor_state (
  name text PRIMARY KEY,
  value text NOT NULL
);
"""


class _Cursor:
    def __init__(self, statements: list[str]):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement: str):
        self.statements.append(statement)


class _Connection:
    def __init__(self, statements: list[str]):
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return _Cursor(self.statements)


class MigrationCompatibilityTests(unittest.TestCase):
    def test_historical_001_remains_the_published_021_schema(self):
        self.assertEqual(LEGACY_021_SCHEMA, (MIGRATIONS / "001_initial.sql").read_text())

    def test_repository_runs_legacy_schema_before_compatible_upgrade(self):
        from replik_monitor.db import PostgresRepository

        statements: list[str] = []
        with patch("replik_monitor.db.psycopg.connect", return_value=_Connection(statements)):
            PostgresRepository("postgresql://unused").migrate()
        self.assertEqual(
            [LEGACY_021_SCHEMA, (MIGRATIONS / "002_change_marker.sql").read_text()],
            statements,
        )

    def test_later_migration_upgrades_legacy_keys_and_constraints(self):
        migration = (MIGRATIONS / "002_change_marker.sql").read_text()
        for statement in (
            "ALTER TABLE changes ADD COLUMN IF NOT EXISTS change_marker text",
            "ALTER TABLE changes DROP CONSTRAINT IF EXISTS changes_pkey",
            "CREATE UNIQUE INDEX IF NOT EXISTS changes_proceeding_state_idx",
            "ALTER TABLE outbox DROP CONSTRAINT IF EXISTS outbox_source_count_check",
            "CHECK (source_count >= 1)",
        ):
            self.assertIn(statement, migration)

    @unittest.skipUnless(
        os.environ.get("REPLIK_TEST_DATABASE_URL"),
        "set REPLIK_TEST_DATABASE_URL to run the real PostgreSQL migration integration test",
    )
    def test_real_postgres_upgrades_exact_021_schema(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from replik_monitor.db import PostgresRepository

        database_url = os.environ["REPLIK_TEST_DATABASE_URL"]
        schema = f"replik_migration_test_{uuid.uuid4().hex}"
        scoped_url = make_conninfo(database_url, options=f"-c search_path={schema}")
        with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(scoped_url) as conn, conn.cursor() as cur:
                cur.execute(LEGACY_021_SCHEMA)
                cur.execute("""INSERT INTO changes(source_id, company_ico, changed_at, title, url)
                               VALUES ('legacy-source', '47251301', now(), 'legacy', 'https://example.invalid')""")
                cur.execute("""INSERT INTO outbox(mode, subject, body, source_count)
                               VALUES ('historical', 'legacy', 'legacy', 500)""")
            PostgresRepository(scoped_url).migrate()
            PostgresRepository(scoped_url).migrate()
            with psycopg.connect(scoped_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT source_id, change_marker FROM changes")
                row = cur.fetchone()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual("legacy-source", row[0])
                self.assertTrue(row[1].startswith("legacy:"))
                cur.execute("""INSERT INTO changes(source_id, change_marker, company_ico, changed_at, title, url)
                               VALUES ('legacy-source', 'new-state', '47251301', now(), 'new', 'https://example.invalid/new')""")
                cur.execute("""INSERT INTO outbox(mode, subject, body, source_count)
                               VALUES ('incremental', 'large', 'large', 501)""")
        finally:
            with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

    @unittest.skipUnless(
        os.environ.get("REPLIK_TEST_DATABASE_URL"),
        "set REPLIK_TEST_DATABASE_URL to run the real PostgreSQL migration integration test",
    )
    def test_real_postgres_fresh_install_has_current_schema(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from replik_monitor.db import PostgresRepository

        database_url = os.environ["REPLIK_TEST_DATABASE_URL"]
        schema = f"replik_fresh_schema_test_{uuid.uuid4().hex}"
        scoped_url = make_conninfo(database_url, options=f"-c search_path={schema}")
        with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            PostgresRepository(scoped_url).migrate()
            PostgresRepository(scoped_url).migrate()
            with psycopg.connect(scoped_url) as conn, conn.cursor() as cur:
                cur.execute("""SELECT is_nullable FROM information_schema.columns
                               WHERE table_schema = current_schema() AND table_name = 'changes'
                                 AND column_name = 'change_marker'""")
                self.assertEqual(("NO",), cur.fetchone())
                cur.execute("""INSERT INTO changes(source_id, change_marker, company_ico, changed_at, title, url)
                               VALUES ('fresh-source', 'state-one', '47251301', now(), 'fresh', 'https://example.invalid/one')""")
                cur.execute("""INSERT INTO changes(source_id, change_marker, company_ico, changed_at, title, url)
                               VALUES ('fresh-source', 'state-two', '47251301', now(), 'fresh', 'https://example.invalid/two')""")
                cur.execute("""INSERT INTO outbox(mode, subject, body, source_count)
                               VALUES ('incremental', 'large', 'large', 501)""")
        finally:
            with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    unittest.main()
