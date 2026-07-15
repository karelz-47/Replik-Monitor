import re
import unittest
from pathlib import Path


class DeploymentArtifactTests(unittest.TestCase):
    def test_schema_has_durable_identity_outbox_and_expiry_state(self):
        migration = Path("migrations/001_initial.sql").read_text()
        for required in ("source_id text NOT NULL", "change_marker text NOT NULL", "changes_proceeding_state_idx", "ingest_id bigserial UNIQUE", "monitor_state", "mode text NOT NULL", "source_count integer NOT NULL CHECK (source_count >= 1)"):
            self.assertIn(required, migration)
        repository = Path("replik_monitor/db.py").read_text()
        for required in ("initial_baseline_started_at", "initial_baseline_complete_at", "ON CONFLICT (name) DO NOTHING RETURNING name"):
            self.assertIn(required, repository)

    def test_docker_and_railway_boot_a_health_checked_non_root_server(self):
        dockerfile, railway = Path("Dockerfile").read_text(), Path("railway.toml").read_text()
        self.assertIn("USER monitor", dockerfile)
        self.assertIn('"serve"', dockerfile)
        self.assertIn('healthcheckPath = "/healthz"', railway)

    def test_no_credential_or_recipient_value_is_packaged(self):
        secret_pattern = re.compile(r"(?:sk_live_|re_[A-Za-z0-9]{8,}|@novis|recipient@example\.com)")
        for path in (Path("README.md"), *Path("replik_monitor").rglob("*.py"), Path("Dockerfile"), Path("railway.toml"), Path("requirements.txt")):
            self.assertIsNone(secret_pattern.search(path.read_text(errors="ignore")), path)
