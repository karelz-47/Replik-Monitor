import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from replik_monitor.config import Settings


class ConfigurationTests(unittest.TestCase):
    def env(self):
        now = datetime.now(UTC)
        return {"DATABASE_URL": "postgresql://unused", "RESEND_API_KEY": "test-key", "RESEND_FROM": "sender-value", "ALERT_TO": "recipient-value", "MONITOR_EXPIRES_AT": (now + timedelta(days=1)).isoformat(), "MONITOR_HISTORICAL_SINCE": (now - timedelta(days=1)).isoformat()}

    def test_settings_require_env_only_values_and_valid_limit(self):
        with patch.dict(os.environ, self.env(), clear=True):
            self.assertEqual(100, Settings.from_env().historical_batch_limit)
        env = self.env() | {"MONITOR_HISTORICAL_BATCH_LIMIT": "501"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "1..100"):
                Settings.from_env()

    def test_deadline_cannot_be_more_than_one_month_away(self):
        env = self.env() | {"MONITOR_EXPIRES_AT": (datetime.now(UTC) + timedelta(days=32)).isoformat()}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "31 days"):
                Settings.from_env()
