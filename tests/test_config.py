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

    def test_email_transport_defaults_to_api_and_validates_resend_smtp_modes(self):
        with patch.dict(os.environ, self.env(), clear=True):
            settings = Settings.from_env()
        self.assertEqual("api", settings.email_transport)
        self.assertEqual("smtp.resend.com", settings.resend_smtp_host)
        self.assertEqual(465, settings.resend_smtp_port)
        self.assertEqual("implicit_tls", settings.resend_smtp_security)

        smtp_env = self.env() | {
            "EMAIL_TRANSPORT": "smtp",
            "RESEND_SMTP_PORT": "587",
            "RESEND_SMTP_SECURITY": "starttls",
        }
        with patch.dict(os.environ, smtp_env, clear=True):
            settings = Settings.from_env()
        self.assertEqual("smtp", settings.email_transport)
        self.assertEqual(587, settings.resend_smtp_port)

        invalid = self.env() | {"EMAIL_TRANSPORT": "smtp", "RESEND_SMTP_PORT": "587"}
        with patch.dict(os.environ, invalid, clear=True):
            with self.assertRaisesRegex(ValueError, "implicit_tls requires"):
                Settings.from_env()

    def test_deadline_cannot_be_more_than_one_month_away(self):
        env = self.env() | {"MONITOR_EXPIRES_AT": (datetime.now(UTC) + timedelta(days=32)).isoformat()}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "31 days"):
                Settings.from_env()
