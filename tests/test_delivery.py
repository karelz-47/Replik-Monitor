import smtplib
import unittest
from email.message import EmailMessage, Message
from types import SimpleNamespace
from urllib.error import HTTPError

from replik_monitor.cli import delivery_adapter
from replik_monitor.delivery import ResendAdapter, ResendSmtpAdapter


class FakeHttpResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class FakeSmtp:
    def __init__(self, host, port, timeout, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ssl_context = context
        self.started_tls = None
        self.login_args = None
        self.message = None
        self.refused = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, *, context):
        self.started_tls = context

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message: EmailMessage):
        self.message = message
        return self.refused


class DeliveryAdapterTests(unittest.TestCase):
    def test_transport_is_environment_selectable_with_api_default_behavior(self):
        settings = SimpleNamespace(
            resend_api_key="test-key",
            resend_from="sender@example.invalid",
            alert_to="recipient@example.invalid",
            email_transport="api",
            resend_smtp_host="smtp.resend.com",
            resend_smtp_port=465,
            resend_smtp_security="implicit_tls",
        )
        self.assertIsInstance(delivery_adapter(settings), ResendAdapter)
        smtp_settings = SimpleNamespace(**(vars(settings) | {"email_transport": "smtp"}))
        self.assertIsInstance(delivery_adapter(smtp_settings), ResendSmtpAdapter)

    def test_api_success_preserves_outbox_idempotency_key(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeHttpResponse(b'{"id":"provider-id"}')

        adapter = ResendAdapter("test-key", "sender@example.invalid", "recipient@example.invalid", opener)
        self.assertEqual("provider-id", adapter.send("subject", "body", "outbox-42"))
        request, timeout = requests[0]
        self.assertEqual(20, timeout)
        self.assertEqual("outbox-42", request.get_header("Idempotency-key"))
        self.assertEqual("Bearer test-key", request.get_header("Authorization"))

    def test_api_failure_propagates_for_durable_retry(self):
        def opener(*_, **__):
            raise HTTPError("https://example.invalid", 403, "blocked", Message(), None)

        adapter = ResendAdapter("test-key", "sender@example.invalid", "recipient@example.invalid", opener)
        with self.assertRaises(HTTPError):
            adapter.send("subject", "body", "outbox-42")

    def test_smtp_implicit_tls_success_uses_stable_message_id(self):
        connections = []

        def factory(*args, **kwargs):
            connection = FakeSmtp(*args, **kwargs)
            connections.append(connection)
            return connection

        adapter = ResendSmtpAdapter(
            "test-key", "sender@example.invalid", "recipient@example.invalid",
            "smtp.resend.com", 465, "implicit_tls", smtp_ssl_factory=factory,
        )
        self.assertEqual(
            "<replik-outbox-outbox-42@replik-monitor.invalid>",
            adapter.send("subject", "body", "outbox-42"),
        )
        connection = connections[0]
        self.assertEqual(("resend", "test-key"), connection.login_args)
        self.assertIsNotNone(connection.ssl_context)
        self.assertIsNone(connection.started_tls)
        self.assertEqual("<replik-outbox-outbox-42@replik-monitor.invalid>", connection.message["Message-ID"])

    def test_smtp_starttls_and_failure_propagate_for_durable_retry(self):
        connections = []

        def factory(*args, **kwargs):
            connection = FakeSmtp(*args, **kwargs)
            connection.refused = {"recipient@example.invalid": (550, b"rejected")}
            connections.append(connection)
            return connection

        adapter = ResendSmtpAdapter(
            "test-key", "sender@example.invalid", "recipient@example.invalid",
            "smtp.resend.com", 587, "starttls", smtp_factory=factory,
        )
        with self.assertRaises(smtplib.SMTPRecipientsRefused):
            adapter.send("subject", "body", "outbox-42")
        self.assertIsNotNone(connections[0].started_tls)
        self.assertEqual(("resend", "test-key"), connections[0].login_args)


if __name__ == "__main__":
    unittest.main()
