import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Settings
from .service import utcnow


def health_http_status(health: dict) -> int:
    """Railway readiness reflects web/database operability, not cron timing."""
    return 200 if health["ok"] else 503


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/healthz", "/checkpoint"}:
            self.send_error(404)
            return
        try:
            from .db import PostgresRepository

            settings = Settings.from_env()
            repository = PostgresRepository(settings.database_url)
            health = repository.health(utcnow(), settings.stale_after_minutes)
            if self.path == "/healthz":
                status, data = health_http_status(health), health
            else:
                status, data = (200, {key: health.get(key) for key in ("operational_status", "checkpoint_status", "last_successful_poll_at", "poll_status", "expired", "expires_at")})
        except ValueError as exc:
            status, data = 503, {"ok": False, "configuration": "invalid", "detail": str(exc)}
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def serve() -> None:
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
