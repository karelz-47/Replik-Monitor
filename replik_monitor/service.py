"""One bounded poll invocation; external scheduling invokes this every 30 minutes."""
from datetime import UTC, datetime, timedelta

COMPANY_ICO = "47251301"


class SyncOverflowError(RuntimeError):
    """The capped no-cursor feed may have omitted records; persistence must stop."""

    def __init__(self, limit: int):
        super().__init__(
            f"REPLIK sync possible overflow at configured limit {limit}; "
            "do not advance checkpoint or deliver a digest; narrow the sync window or raise the approved limit"
        )
        self.limit = limit


def overflow_status(error: SyncOverflowError) -> dict:
    """Safe scheduler output: no SOAP payload, case IDs, titles, or recipient data."""
    return {
        "status": "sync-overflow",
        "limit": error.limit,
        "action": "narrow the sync window or raise the approved limit, then rerun",
    }


def utcnow() -> datetime:
    return datetime.now(UTC)


def poll_once(client, repository, settings, now: datetime | None = None) -> dict:
    now = now or utcnow()
    expiry = repository.ensure_active(settings.expires_at, now)
    lifecycle = repository.initial_lifecycle()
    # A historical baseline is a transactionally claimed one-shot operation.  While
    # its durable outbox item is pending or retrying, do not fetch/emit a second digest.
    if lifecycle == "awaiting-initial-delivery":
        return {"mode": "initial-delivery-pending", "fetched": 0, "inserted": 0,
                "checkpoint": repository.checkpoint().isoformat() if repository.checkpoint() else None,
                "expires_at": expiry.isoformat()}
    initial = lifecycle == "new"
    since = settings.historical_since if initial else (repository.checkpoint() or now) - timedelta(minutes=settings.poll_overlap_minutes)
    fetched = client.fetch_changes(COMPANY_ICO, since, settings.historical_batch_limit)
    # A response equal to the caller-provided cap is ambiguous because this official
    # operation has no cursor/page token. Fail closed before database/outbox mutation.
    if fetched.response_count >= settings.historical_batch_limit:
        raise SyncOverflowError(settings.historical_batch_limit)
    changes = fetched.changes
    # Deterministic presentation avoids a provider/API ordering accident changing a digest.
    changes.sort(key=lambda item: (item.changed_at, item.source_id))
    if settings.dry_run:
        return {"mode": "dry-run", "phase": "initial" if initial else "incremental", "fetched": len(changes), "inserted": 0, "expires_at": expiry.isoformat()}
    if initial:
        inserted = repository.record_initial(changes, now, settings.historical_batch_limit)
        mode = "initial-historical"
    else:
        inserted = repository.record_incremental(changes, now)
        mode = "incremental-identity-cursor"
    return {"mode": mode, "fetched": len(changes), "inserted": inserted, "checkpoint": now.isoformat(), "expires_at": expiry.isoformat()}
