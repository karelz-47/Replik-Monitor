"""PostgreSQL persistence: migration, single-run lock, identity cursor and durable digest outbox."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import psycopg

from .domain import Change


class ExpiredMonitorError(RuntimeError):
    pass


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def format_digest(changes: Iterable[Change], mode: str) -> tuple[str, str]:
    rows = list(changes)
    subject = f"Replik {mode} digest ({len(rows)} records)"
    # Link-first; source payloads are deliberately excluded.
    body = "\n".join(f"{change.url}\n{change.title}\nChanged: {_iso(change.changed_at)}" for change in rows)
    return subject, body


class PostgresRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def migrate(self) -> None:
        migrations = sorted(Path(__file__).parent.parent.joinpath("migrations").glob("*.sql"))
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            for migration in migrations:
                cur.execute(migration.read_text())

    @contextmanager
    def run_lock(self):
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext('replik-monitor-poll'))")
            acquired = cur.fetchone()[0]
            try:
                yield acquired
            finally:
                if acquired:
                    cur.execute("SELECT pg_advisory_unlock(hashtext('replik-monitor-poll'))")

    def ensure_active(self, configured_expiry: datetime, now: datetime) -> datetime:
        """Persist the first deadline; later env edits cannot extend the monitor lifetime."""
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT value::timestamptz FROM monitor_state WHERE name='expires_at'")
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO monitor_state(name, value) VALUES ('expires_at', %s)", (_iso(configured_expiry),))
                expiry = configured_expiry
            else:
                expiry = row[0]
            if now >= expiry:
                raise ExpiredMonitorError("monitor expiry deadline reached; poll and delivery are disabled")
            return expiry

    def initial_lifecycle(self) -> str:
        """Return the durable initial-baseline phase without relying on delivery timing."""
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM monitor_state WHERE name IN (%s, %s)",
                        ("initial_baseline_started_at", "initial_baseline_complete_at"))
            names = {row[0] for row in cur.fetchall()}
        if "initial_baseline_complete_at" in names:
            return "incremental"
        if "initial_baseline_started_at" in names:
            return "awaiting-initial-delivery"
        return "new"

    def checkpoint(self) -> datetime | None:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM checkpoints WHERE name = 'poll'")
            row = cur.fetchone()
            return row[0] if row else None

    def record_initial(self, changes: list[Change], checkpoint: datetime, limit: int) -> int:
        # Claim the one-shot baseline and persist its changes/outbox in one transaction.
        # A crash rolls all of this back; a later poll can then safely make the first claim.
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO monitor_state(name, value)
                           VALUES ('initial_baseline_started_at', now()::text)
                           ON CONFLICT (name) DO NOTHING RETURNING name""")
            if cur.fetchone() is None:
                return 0
            inserted: list[Change] = []
            for change in changes:
                cur.execute("""INSERT INTO changes(source_id, change_marker, company_ico, changed_at, title, url)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (source_id, change_marker) DO NOTHING RETURNING source_id""",
                    (change.source_id, change.change_marker, change.company_ico, change.changed_at, change.title, change.url))
                if cur.fetchone():
                    inserted.append(change)
            if inserted:
                subject, body = format_digest(inserted, "historical")
                cur.execute("""INSERT INTO outbox(mode, subject, body, source_count)
                               VALUES ('historical', %s, %s, %s)""", (subject, body, len(inserted)))
            else:
                # No email exists to acknowledge. This must be durable before later polls
                # choose the incremental path.
                cur.execute("""INSERT INTO monitor_state(name, value)
                               VALUES ('initial_baseline_complete_at', now()::text)""")
            cur.execute("""INSERT INTO checkpoints(name, value) VALUES ('poll', %s)
                ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (checkpoint,))
            return len(inserted)

    def record_incremental(self, changes: list[Change], checkpoint: datetime) -> int:
        return self._record(changes, checkpoint, mode="incremental", create_digest=True)

    def _record(self, changes: list[Change], checkpoint: datetime, mode: str, create_digest: bool) -> int:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            inserted: list[Change] = []
            for change in changes:
                cur.execute("""INSERT INTO changes(source_id, change_marker, company_ico, changed_at, title, url)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (source_id, change_marker) DO NOTHING RETURNING source_id""",
                    (change.source_id, change.change_marker, change.company_ico, change.changed_at, change.title, change.url))
                if cur.fetchone():
                    inserted.append(change)
            # The database insertion identity is the incremental cursor. The source_id
            # unique constraint is the external identity cursor that makes overlap safe.
            if inserted and create_digest:
                subject, body = format_digest(inserted, mode)
                cur.execute("""INSERT INTO outbox(mode, subject, body, source_count)
                               VALUES (%s,%s,%s,%s)""", (mode, subject, body, len(inserted)))
            cur.execute("""INSERT INTO checkpoints(name, value) VALUES ('poll', %s)
                ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (checkpoint,))
            return len(inserted)

    def initial_digest_acknowledged(self) -> None:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO monitor_state(name, value)
                           VALUES ('initial_baseline_complete_at', now()::text)
                           ON CONFLICT (name) DO NOTHING""")

    def deliver_pending(self, adapter, now: datetime) -> int:
        delivered = 0
        while True:
            with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
                cur.execute("""SELECT id, mode, subject, body FROM outbox
                    WHERE status IN ('pending','failed','sending') AND next_attempt_at <= now()
                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""")
                row = cur.fetchone()
                if not row:
                    return delivered
                outbox_id, mode, subject, body = row
                cur.execute("UPDATE outbox SET status='sending', attempts=attempts+1, next_attempt_at=now() + interval '5 minutes' WHERE id=%s", (outbox_id,))
            try:
                provider_id = adapter.send(subject, body, str(outbox_id))
            except Exception as exc:
                with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
                    cur.execute("""UPDATE outbox SET status='failed', last_error=%s,
                        next_attempt_at=now() + (interval '1 minute' * LEAST(60, power(2, attempts)::int)) WHERE id=%s""",
                        (type(exc).__name__[:80], outbox_id))
                continue
            with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
                cur.execute("UPDATE outbox SET status='sent', provider_id=%s, sent_at=now(), last_error=NULL WHERE id=%s", (provider_id, outbox_id))
                if mode == "historical":
                    cur.execute("""INSERT INTO monitor_state(name, value)
                                   VALUES ('initial_baseline_complete_at', now()::text)
                                   ON CONFLICT (name) DO NOTHING""")
                delivered += 1

    def health(self, now: datetime, stale_after_minutes: int) -> dict:
        try:
            with psycopg.connect(self.database_url, connect_timeout=3) as conn, conn.cursor() as cur:
                cur.execute("SELECT value::timestamptz FROM monitor_state WHERE name='expires_at'")
                expires = cur.fetchone()
                cur.execute("SELECT value FROM checkpoints WHERE name='poll'")
                checkpoint = cur.fetchone()
        except psycopg.Error:
            return {"ok": False, "database": "unreachable", "operational_status": "unavailable", "checkpoint_status": "unknown", "poll_status": "unknown"}
        expiry = expires[0] if expires else None
        last = checkpoint[0] if checkpoint else None
        expired = expiry is not None and now >= expiry
        stale = last is None or now - last > timedelta(minutes=stale_after_minutes)
        checkpoint_status = "missing" if last is None else ("stale" if stale else "fresh")
        # The web process is deploy-ready once its configured database is reachable.
        # Checkpoint freshness is intentionally a separate operational signal: a fresh
        # web service must not fail Railway's healthcheck before the first 30-minute cron.
        return {"ok": not expired, "database": "reachable",
                "operational_status": "expired" if expired else "ready", "expired": expired,
                "expires_at": _iso(expiry) if expiry else None,
                "last_successful_poll_at": _iso(last) if last else None,
                "checkpoint_status": checkpoint_status, "poll_status": checkpoint_status}
