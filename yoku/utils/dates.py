"""Shared timestamp helpers — the one place ISO parsing and week math live.

Source timestamps are heterogeneous ISO strings ('2026-04-30T07:09:03Z',
'2026-05-29T00:15:12.161-0700'); these helpers normalize them once so
metrics, baselines, and detectors can't drift apart on parsing rules.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string (Z or offset) to an aware datetime, else None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def week_monday(dt: datetime) -> str:
    """ISO date of the Monday starting `dt`'s week — the weekly-series key."""
    return (dt - timedelta(days=dt.weekday())).date().isoformat()


def days_since(value: str | None, now: datetime) -> int:
    """Whole days between an ISO timestamp and `now` (0 for unparseable/future)."""
    ts = parse_iso(value)
    if ts is None:
        return 0
    return max(0, (now - ts).days)
