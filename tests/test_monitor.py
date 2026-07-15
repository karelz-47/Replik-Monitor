import unittest
from datetime import UTC, datetime, timedelta

from replik_monitor.client import FetchChangesResult
from replik_monitor.config import Settings
from replik_monitor.domain import Change
from replik_monitor.http import health_http_status
from replik_monitor.service import SyncOverflowError, overflow_status, poll_once


class FakeClient:
    def __init__(self, changes, response_count=None):
        self.changes, self.response_count, self.since, self.calls = changes, response_count, None, 0

    def fetch_changes(self, ico, since, max_results):
        self.since, self.calls = since, self.calls + 1
        self.max_results = max_results
        count = self.response_count if self.response_count is not None else len(self.changes)
        return FetchChangesResult(list(self.changes), count)


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
        self.settings = Settings("db", "endpoint", "portal", "key-value", "sender-value", "recipient-value", now + timedelta(days=1), now - timedelta(days=7), 2, 10, 90)
        self.a = Change("b", "47251301", now - timedelta(hours=1), "B", "https://example.test/b")
        self.b = Change("a", "47251301", now - timedelta(hours=2), "A", "https://example.test/a")

    def test_initial_poll_uses_history_and_sorted_single_bounded_digest(self):
        # Two returned rows are non-overflow only under a cap greater than two.
        settings = self.settings.__class__(**(self.settings.__dict__ | {"historical_batch_limit": 3}))
        repo, client = LifecycleRepo(), FakeClient([self.a, self.b])
        result = poll_once(client, repo, settings, self.now)
        self.assertEqual(settings.historical_since, client.since)
        self.assertEqual("initial-historical", result["mode"])
        self.assertEqual("initial", repo.calls[0][0])
        self.assertEqual(["a", "b"], [row.source_id for row in repo.calls[0][1]])
        self.assertEqual(3, repo.calls[0][2])

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

    def test_capped_initial_sync_fails_closed_before_baseline_checkpoint_or_outbox(self):
        repo, client = LifecycleRepo(), FakeClient([self.a], response_count=2)
        with self.assertRaisesRegex(SyncOverflowError, "possible overflow"):
            poll_once(client, repo, self.settings, self.now)
        self.assertEqual([], repo.calls)
        self.assertIsNone(repo.checkpoint())
        self.assertEqual("new", repo.initial_lifecycle())

    def test_capped_incremental_sync_fails_closed_before_checkpoint_or_delivery_state(self):
        repo, client = LifecycleRepo(), FakeClient([self.a], response_count=2)
        repo.lifecycle = "incremental"
        repo._checkpoint = self.now - timedelta(minutes=20)
        with self.assertRaisesRegex(SyncOverflowError, "possible overflow"):
            poll_once(client, repo, self.settings, self.now)
        self.assertEqual([], repo.calls)
        self.assertEqual(self.now - timedelta(minutes=20), repo.checkpoint())
        self.assertEqual("incremental", repo.initial_lifecycle())

    def test_non_overflow_sync_records_normally_even_when_filtered_result_count_is_smaller(self):
        repo, client = LifecycleRepo(), FakeClient([self.a], response_count=1)
        result = poll_once(client, repo, self.settings, self.now)
        self.assertEqual("initial-historical", result["mode"])
        self.assertEqual(["initial"], [call[0] for call in repo.calls])

    def test_sync_overflow_status_is_redacted_and_actionable(self):
        status = overflow_status(SyncOverflowError(2))
        self.assertEqual("sync-overflow", status["status"])
        self.assertEqual(2, status["limit"])
        self.assertIn("narrow the sync window", status["action"])
        self.assertNotIn("source_id", status)
        self.assertNotIn("changes", status)

    def test_fresh_web_service_is_deploy_ready_before_the_first_checkpoint(self):
        health = {"ok": True, "database": "reachable", "operational_status": "ready", "checkpoint_status": "missing"}
        self.assertEqual(200, health_http_status(health))
        self.assertEqual("missing", health["checkpoint_status"])
