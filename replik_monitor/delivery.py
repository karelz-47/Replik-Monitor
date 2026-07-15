"""Resend HTTP adapter. It only sends when explicitly invoked by a ready outbox item."""
import json
from urllib.request import Request, urlopen


class ResendAdapter:
    endpoint = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str, recipient: str):
        self.api_key, self.sender, self.recipient = api_key, sender, recipient

    def send(self, subject: str, text: str, idempotency_key: str) -> str:
        body = json.dumps({"from": self.sender, "to": [self.recipient], "subject": subject, "text": text}).encode()
        request = Request(self.endpoint, body, {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Idempotency-Key": idempotency_key}, method="POST")
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
        return data["id"]
