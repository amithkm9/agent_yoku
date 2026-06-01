"""Per-source data freshness: document counts plus when each connector last
synced. Shared by the agent's `data_freshness` tool and the stats API so both
report the same numbers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pymongo.collection import Collection

from yoku.db import connector_configs as cc
from yoku.db.mongo import (
    dc_github_collection,
    dc_jira_collection,
    dc_slack_collection,
)

# Source name -> its primary dc-* collection accessor.
# Accessed directly (not via get_collection) because dc-* collections are
# internal and not in ALLOWED_COLLECTIONS.
_SOURCE_COLLECTIONS: dict[str, Callable[[], Collection]] = {
    "jira": dc_jira_collection,
    "github": dc_github_collection,
    "slack": dc_slack_collection,
}


def _ago(seconds: float) -> str:
    """Coarse relative age, good enough for a freshness hint."""
    s = int(seconds)
    if s < 60:
        return "just now"
    minutes = s // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def source_freshness() -> list[dict]:
    """One row per data source: count, last sync time, relative age, status.

    Pulls sync timing from each connector's config doc. Always returns a row for
    every supported source — even unconfigured ones (last_synced_at=None) — so
    callers can tell "unsynced" apart from "genuinely empty".
    """
    configs = {c["name"]: c for c in cc.list_configs()}
    now = datetime.now(UTC)
    rows: list[dict] = []
    for name, coll_factory in _SOURCE_COLLECTIONS.items():
        cfg = configs.get(name) or {}
        ts = cfg.get("last_synced_at")
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        rows.append(
            {
                "source": name,
                "count": coll_factory().estimated_document_count(),
                "last_synced_at": ts.isoformat() if ts else None,
                "synced_ago": _ago((now - ts).total_seconds()) if ts else "never",
                "last_sync_status": cfg.get("last_sync_status"),
            }
        )
    return rows
