"""Environment-only configuration. Values are never logged."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os

from .client import DEFAULT_ENDPOINT, DEFAULT_PORTAL_URL

MAX_MONITOR_LIFETIME = timedelta(days=31)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("MONITOR_EXPIRES_AT must include a UTC offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class Settings:
    database_url: str
    replik_endpoint: str
    replik_portal_url: str
    resend_api_key: str
    resend_from: str
    alert_to: str
    expires_at: datetime
    historical_since: datetime
    historical_batch_limit: int
    poll_overlap_minutes: int
    stale_after_minutes: int
    dry_run: bool = False
    email_transport: str = "api"
    resend_smtp_host: str = "smtp.resend.com"
    resend_smtp_port: int = 465
    resend_smtp_security: str = "implicit_tls"

    @classmethod
    def from_env(cls) -> "Settings":
        required = ("DATABASE_URL", "RESEND_API_KEY", "RESEND_FROM", "ALERT_TO", "MONITOR_EXPIRES_AT", "MONITOR_HISTORICAL_SINCE")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError("missing required configuration: " + ", ".join(missing))
        expires_at = parse_utc(os.environ["MONITOR_EXPIRES_AT"])
        if expires_at <= datetime.now(UTC) or expires_at > datetime.now(UTC) + MAX_MONITOR_LIFETIME:
            raise ValueError("MONITOR_EXPIRES_AT must be future and no more than 31 days away")
        limit = int(os.getenv("MONITOR_HISTORICAL_BATCH_LIMIT", "100"))
        if not 1 <= limit <= 100:
            raise ValueError("MONITOR_HISTORICAL_BATCH_LIMIT must be 1..100 (REPLIK page maximum)")
        overlap = int(os.getenv("POLL_OVERLAP_MINUTES", "10"))
        stale = int(os.getenv("MONITOR_STALE_AFTER_MINUTES", "90"))
        if overlap < 0 or stale < 1:
            raise ValueError("POLL_OVERLAP_MINUTES must be non-negative and MONITOR_STALE_AFTER_MINUTES positive")
        transport = os.getenv("EMAIL_TRANSPORT", "api").lower()
        if transport not in {"api", "smtp"}:
            raise ValueError("EMAIL_TRANSPORT must be api or smtp")
        smtp_host = os.getenv("RESEND_SMTP_HOST", "smtp.resend.com")
        if not smtp_host:
            raise ValueError("RESEND_SMTP_HOST must not be empty")
        try:
            smtp_port = int(os.getenv("RESEND_SMTP_PORT", "465"))
        except ValueError as exc:
            raise ValueError("RESEND_SMTP_PORT must be an integer") from exc
        if smtp_port not in {25, 465, 587, 2465, 2587}:
            raise ValueError("RESEND_SMTP_PORT must be one of 25, 465, 587, 2465, 2587")
        smtp_security = os.getenv("RESEND_SMTP_SECURITY", "implicit_tls").lower()
        if smtp_security not in {"implicit_tls", "starttls"}:
            raise ValueError("RESEND_SMTP_SECURITY must be implicit_tls or starttls")
        if smtp_security == "implicit_tls" and smtp_port not in {465, 2465}:
            raise ValueError("implicit_tls requires RESEND_SMTP_PORT 465 or 2465")
        if smtp_security == "starttls" and smtp_port not in {25, 587, 2587}:
            raise ValueError("starttls requires RESEND_SMTP_PORT 25, 587, or 2587")
        return cls(
            database_url=os.environ["DATABASE_URL"],
            replik_endpoint=os.getenv("REPLIK_ENDPOINT", DEFAULT_ENDPOINT),
            replik_portal_url=os.getenv("REPLIK_PORTAL_URL", DEFAULT_PORTAL_URL),
            resend_api_key=os.environ["RESEND_API_KEY"],
            resend_from=os.environ["RESEND_FROM"],
            alert_to=os.environ["ALERT_TO"],
            email_transport=transport,
            resend_smtp_host=smtp_host,
            resend_smtp_port=smtp_port,
            resend_smtp_security=smtp_security,
            expires_at=expires_at,
            historical_since=parse_utc(os.environ["MONITOR_HISTORICAL_SINCE"]),
            historical_batch_limit=limit,
            poll_overlap_minutes=overlap,
            stale_after_minutes=stale,
            dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
        )
