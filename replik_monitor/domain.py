"""Domain types and deterministic alert rendering."""
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class Change:
    source_id: str
    company_ico: str
    changed_at: datetime
    title: str
    url: str


def select_unseen(changes: Iterable[Change], known_source_ids: set[str]) -> list[Change]:
    return [change for change in changes if change.source_id not in known_source_ids]


def format_alert(change: Change) -> str:
    # Deliberately link-first: no source payload or untrusted free-form data beyond title.
    return f"New Replik record: {change.title}\n{change.url}\nChanged: {change.changed_at.isoformat()}"
