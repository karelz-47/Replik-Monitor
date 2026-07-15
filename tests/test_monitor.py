import unittest
from datetime import UTC, datetime, timedelta

from replik_monitor.config import Settings
from replik_monitor.domain import Change
from replik_monitor.http import health_http_status
from replik_monitor.service import poll_once


class FakeClient:
    def __init__(self, changes):
        self.changes, self.since, self.calls = changes, None, 0

    def fetch_changes(self, ico, since):
        self.since, self.calls = since, self.calls + 1
        return list(self.changes)


class LifecycleRepo:
    """In-memory lifecycle seam: models one durable baseline/outbox transaction."""
    def __init__(self):
        self.lifecycle, self.calls, self._checkpoint = "new", [], None

    def ensure_active(self, expiry, now):
        return expiry

    def initial_lifecycle(self):
        return self.lifecycle

    def checkpoint(self):
        return self._checkpoint

    def record_initial(self, changes, checkpoint, limit):
        if self.lifecycle != "new":
            raise AssertionError("a historical baseline may be recorded only once")
        self.calls.append(("initial", changes, limit))
        self._checkpoint = checkpoint
        self.lifecycle = "incremental" if not changes else "awaiting-initial-delivery"
        return len(changes)

    def record_incremental(self, changes, checkpoint):
        self.calls.append(("incremental", changes))
        self._checkpoint = checkpoint
        return len(changes)

    def acknowledge_initial_delivery(self):
        if self.lifecycle != "awaiting-initial-delivery":
            raise AssertionError("no pending initial digest")
        self.lifecycle = "incremental"


class MonitorLifecycleTests(unittest.TestCase):
    def setUp(self):
        now = datetime(2026, 7, 15, 12, tzinfo=UTC)
        self.now = now
        self.settings = Settings("db", "endpoint", "", "key-value", "sender-value", "recipient-value", now + timedelta(days=1), now - timedelta(days=7), 2, 10, 90)
        self.a = Change("b", "47251301", now - timedelta(hours=1), "B", "https://example.test/b")
        self.b = Change("a", "47251301", now - timedelta(hours=2), "A", "https://example.test/a")

    def test_initial_poll_uses_history_and_sorted_single_bounded_digest(self):
        repo, client = LifecycleRepo(), FakeClient([self.a, self.b])
        result = poll_once(client, repo, self.settings, self.now)
        self.assertEqual(self.settings.historical_since, client.since)
        self.assertEqual("initial-historical", result["mode"])
        self.assertEqual("initial", repo.calls[0][0])
        self.assertEqual(["a", "b"], [row.source_id for row in repo.calls[0][1]])
        self.assertEqual(2, repo.calls[0][2])

    def test_pending_or_failed_initial_delivery_never_creates_a_second_historical_digest(self):
        repo, client = LifecycleRepo(), FakeClient([self.a])
        poll_once(client, repo, self.settings, self.now)
        delayed = poll_once(client, repo, self.settings, self.now + timedelta(minutes=30))
        # A failed outbox delivery leaves the same durable awaiting state, so the next
        # cron run also waits rather than producing another historical digest.
        failed = poll_once(client, repo, self.settings, self.now + timedelta(minutes=60))
        self.assertEqual(["initial"], [call[0] for call in repo.calls])
        self.assertEqual(1, client.calls)
        self.assertEqual("initial-delivery-pending", delayed["mode"])
        self.assertEqual("initial-delivery-pending", failed["mode"])

    def test_empty_successful_baseline_durably_enters_incremental_mode(self):
        repo, client = LifecycleRepo(), FakeClient([])
        initial = poll_once(client, repo, self.settings, self.now)
        client.changes = [self.a]
        later = poll_once(client, repo, self.settings, self.now + timedelta(minutes=30))
        self.assertEqual("initial-historical", initial["mode"])
        self.assertEqual("incremental-identity-cursor", later["mode"])
        self.assertEqual(["initial", "incremental"], [call[0] for call in repo.calls])

    def test_delivered_initial_baseline_then_uses_checkpoint_overlap_and_incremental_identity_path(self):
        repo, client = LifecycleRepo(), FakeClient([self.a])
        poll_once(client, repo, self.settings, self.now)
        repo.acknowledge_initial_delivery()
        result = poll_once(client, repo, self.settings, self.now + timedelta(minutes=20))
        self.assertEqual(self.now - timedelta(minutes=10), client.since)
        self.assertEqual("incremental-identity-cursor", result["mode"])
        self.assertEqual("incremental", repo.calls[1][0])

    def test_dry_run_is_non_persistent(self):
        settings = self.settings.__class__(**(self.settings.__dict__ | {"dry_run": True}))
        repo = LifecycleRepo()
        result = poll_once(FakeClient([self.a]), repo, settings, self.now)
        self.assertEqual("dry-run", result["mode"])
        self.assertEqual([], repo.calls)

    def test_fresh_web_service_is_deploy_ready_before_the_first_checkpoint(self):
        health = {"ok": True, "database": "reachable", "operational_status": "ready", "checkpoint_status": "missing"}
        self.assertEqual(200, health_http_status(health))
        self.assertEqual("missing", health["checkpoint_status"])
