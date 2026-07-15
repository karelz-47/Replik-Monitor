import unittest
from datetime import UTC, datetime, timedelta

from replik_monitor.client import FetchChangesResult
from replik_monitor.config import Settings
from replik_monitor.domain import Change
from replik_monitor.service import poll_once


class FakeClient:
    def __init__(self, changes, response_count=None):
        self.changes, self.response_count, self.since, self.calls = changes, response_count, None, 0
    def fetch_changes(self, ico, since, max_results):
        self.since, self.calls, self.max_results = since, self.calls + 1, max_results
        return FetchChangesResult(list(self.changes), self.response_count if self.response_count is not None else len(self.changes))


class LifecycleRepo:
    def __init__(self): self.lifecycle, self.calls, self._checkpoint = "new", [], None
    def ensure_active(self, expiry, now): return expiry
    def initial_lifecycle(self): return self.lifecycle
    def checkpoint(self): return self._checkpoint
    def record_initial(self, changes, checkpoint, limit):
        self.calls.append(("initial", changes, limit)); self._checkpoint = checkpoint
        self.lifecycle = "incremental" if not changes else "awaiting-initial-delivery"; return len(changes)
    def record_incremental(self, changes, checkpoint):
        self.calls.append(("incremental", changes)); self._checkpoint = checkpoint; return len(changes)
    def acknowledge_initial_delivery(self): self.lifecycle = "incremental"


class MonitorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 15, 12, tzinfo=UTC)
        self.settings = Settings("db", "endpoint", "portal", "key", "sender", "recipient", self.now + timedelta(days=1), self.now - timedelta(days=7), 100, 10, 90)
        self.a = Change("b", "47251301", self.now - timedelta(hours=1), "B", "https://example.test/b", "state-b")
        self.b = Change("a", "47251301", self.now - timedelta(hours=2), "A", "https://example.test/a", "state-a")

    def test_initial_poll_accepts_ico_total_over_500_and_creates_one_sorted_digest(self):
        changes = [Change(str(i), "47251301", self.now, "case", f"https://example.test/{i}", f"state-{i}") for i in range(501)]
        repo, client = LifecycleRepo(), FakeClient(changes, response_count=501)
        result = poll_once(client, repo, self.settings, self.now)
        self.assertEqual("initial-historical", result["mode"])
        self.assertEqual(501, result["inserted"])
        self.assertEqual(1, len(repo.calls))
        self.assertEqual(100, client.max_results)

    def test_pending_initial_delivery_is_idempotent(self):
        repo, client = LifecycleRepo(), FakeClient([self.a])
        poll_once(client, repo, self.settings, self.now)
        delayed = poll_once(client, repo, self.settings, self.now + timedelta(minutes=30))
        self.assertEqual(1, client.calls)
        self.assertEqual("initial-delivery-pending", delayed["mode"])

    def test_repeated_proceeding_with_new_marker_reaches_incremental_repository(self):
        repo, client = LifecycleRepo(), FakeClient([self.a])
        poll_once(client, repo, self.settings, self.now); repo.acknowledge_initial_delivery()
        amended = Change("b", "47251301", self.now, "B amended", "https://example.test/b", "state-b-new")
        client.changes = [amended]
        result = poll_once(client, repo, self.settings, self.now + timedelta(minutes=30))
        self.assertEqual("incremental-proceeding-state", result["mode"])
        self.assertEqual("state-b-new", repo.calls[-1][1][0].change_marker)

    def test_dry_run_is_non_persistent(self):
        settings = self.settings.__class__(**(self.settings.__dict__ | {"dry_run": True}))
        repo = LifecycleRepo()
        self.assertEqual("dry-run", poll_once(FakeClient([self.a]), repo, settings, self.now)["mode"])
        self.assertEqual([], repo.calls)


if __name__ == "__main__": unittest.main()
