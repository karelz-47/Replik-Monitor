"""Resend delivery adapters; payloads and credentials are never logged."""
from __future__ import annotations

from email.message import EmailMessage
import json
import smtplib
import ssl
from typing import Callable
from urllib.request import Request, urlopen


class ResendAdapter:
    """Resend HTTP API adapter, retained as the backwards-compatible default."""

    endpoint = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str, recipient: str,
                 opener: Callable = urlopen):
        self.api_key = api_key
        self.sender = sender
        self.recipient = recipient
        self._opener = opener

    def send(self, subject: str, text: str, idempotency_key: str) -> str:
        body = json.dumps({
            "from": self.sender,
            "to": [self.recipient],
            "subject": subject,
            "text": text,
        }).encode()
        request = Request(
            self.endpoint,
            body,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        with self._opener(request, timeout=20) as response:
            data = json.loads(response.read())
        return data["id"]


class ResendSmtpAdapter:
    """Resend SMTP adapter using an API key as the documented SMTP password.

    Resend's documented ``Resend-Idempotency-Key`` SMTP header carries the stable
    outbox delivery key for provider-side deduplication. A deterministic Message-ID
    remains useful for recipient and downstream-system correlation.
    """

    username = "resend"

    def __init__(self, api_key: str, sender: str, recipient: str, host: str,
                 port: int, security: str, smtp_ssl_factory: Callable = smtplib.SMTP_SSL,
                 smtp_factory: Callable = smtplib.SMTP,
                 ssl_context_factory: Callable[[], ssl.SSLContext] = ssl.create_default_context):
        self.api_key = api_key
        self.sender = sender
        self.recipient = recipient
        self.host = host
        self.port = port
        self.security = security
        self._smtp_ssl_factory = smtp_ssl_factory
        self._smtp_factory = smtp_factory
        self._ssl_context_factory = ssl_context_factory

    def send(self, subject: str, text: str, idempotency_key: str) -> str:
        message_id = f"<replik-outbox-{idempotency_key}@replik-monitor.invalid>"
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = self.recipient
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message["Resend-Idempotency-Key"] = idempotency_key
        message.set_content(text)
        context = self._ssl_context_factory()

        if self.security == "implicit_tls":
            connection_factory = self._smtp_ssl_factory
        elif self.security == "starttls":
            connection_factory = self._smtp_factory
        else:
            raise ValueError("RESEND_SMTP_SECURITY must be implicit_tls or starttls")

        with connection_factory(
            self.host,
            self.port,
            timeout=20,
            **({"context": context} if self.security == "implicit_tls" else {}),
        ) as connection:
            if self.security == "starttls":
                connection.starttls(context=context)
            connection.login(self.username, self.api_key)
            refused = connection.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
        return message_id
