"""Explicit migrate, poll, deliver and health-server entry points."""
import argparse
import json

from .client import ReplikSoapClient
from .config import Settings
from .db import ExpiredMonitorError, PostgresRepository
from .delivery import ResendAdapter
from .http import serve
from .service import poll_once, utcnow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "poll", "deliver", "serve"])
    args = parser.parse_args()
    if args.command == "serve":
        serve()
        return
    settings = Settings.from_env()
    repository = PostgresRepository(settings.database_url)
    if args.command == "migrate":
        repository.migrate()
        print(json.dumps({"status": "migrated"}))
        return
    try:
        if args.command == "poll":
            with repository.run_lock() as acquired:
                if not acquired:
                    print(json.dumps({"status": "skipped", "reason": "another run holds advisory lock"}))
                    return
                client = ReplikSoapClient(settings.replik_endpoint, settings.replik_portal_url)
                print(json.dumps(poll_once(client, repository, settings, utcnow())))
            return
        repository.ensure_active(settings.expires_at, utcnow())
        adapter = ResendAdapter(settings.resend_api_key, settings.resend_from, settings.alert_to)
        print(json.dumps({"delivered": repository.deliver_pending(adapter, utcnow())}))
    except ExpiredMonitorError as exc:
        print(json.dumps({"status": "expired", "detail": str(exc)}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
