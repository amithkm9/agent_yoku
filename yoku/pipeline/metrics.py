"""Weekly trend metrics — build-plan M4, riding the M2 events stream.

Computes one row per (metric, week) into `ds-metrics`, idempotently
(recomputes the trailing window every run; upserts on the unique key).

Two sources, deliberately:
- **Documents** give full history where timestamps survive sync overwrites:
  PR `created`/`merged_at` and ticket `created`.
- **Events** give what documents can't: status transitions. JIRA docs carry
  only the *current* status, so `tickets_done` counts status→done-state
  `updated` events and accumulates from when event capture went live.

`signals_open` snapshots the proactive gap count into the current week each
run — the trend of how much drift the team carries.

Weeks are keyed by the ISO date of their Monday ("2026-06-08").
"""

from __future__ import annotations

import statistics
import time
from datetime import UTC, datetime, timedelta

from pymongo import UpdateOne

from yoku.constants import DEFAULT_DONE_STATUSES
from yoku.db.mongo import (
    dc_github_collection,
    dc_jira_collection,
    ds_metrics_collection,
    events_collection,
    signals_collection,
)
from yoku.logging import get_logger

log = get_logger("metrics")

DEFAULT_WEEKS = 26


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _monday(dt: datetime) -> str:
    return (dt - timedelta(days=dt.weekday())).date().isoformat()


def _weeks_in_window(now: datetime, weeks: int) -> list[str]:
    last = datetime.fromisoformat(_monday(now)).replace(tzinfo=UTC)
    return [(last - timedelta(weeks=i)).date().isoformat() for i in range(weeks - 1, -1, -1)]


def _weekly_counts(timestamps: list[datetime]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ts in timestamps:
        week = _monday(ts)
        counts[week] = counts.get(week, 0) + 1
    return counts


def compute_metrics(weeks: int = DEFAULT_WEEKS, now: datetime | None = None) -> dict[str, int]:
    """Recompute the trailing `weeks` of every series. Returns rows-per-metric."""
    now = now or datetime.now(UTC)
    t0 = time.monotonic()
    window = _weeks_in_window(now, weeks)
    cutoff = window[0]  # ISO date prefix — string-compares correctly with ISO timestamps

    rows: list[dict] = []

    # --- PRs: opened, merged, cycle time (full history from doc timestamps) ---
    opened: list[datetime] = []
    for d in dc_github_collection().find({"created": {"$gte": cutoff}}, {"created": 1}):
        if ts := _parse_ts(d.get("created")):
            opened.append(ts)

    merged: list[datetime] = []
    cycle_days: dict[str, list[float]] = {}
    for d in dc_github_collection().find(
        {"merged_at": {"$gte": cutoff}}, {"merged_at": 1, "created": 1}
    ):
        m = _parse_ts(d.get("merged_at"))
        if m is None:
            continue
        merged.append(m)
        c = _parse_ts(d.get("created"))
        if c is not None and m >= c:
            cycle_days.setdefault(_monday(m), []).append((m - c).total_seconds() / 86_400)

    # --- Tickets created (full history) ---
    t_created: list[datetime] = []
    for d in dc_jira_collection().find({"created": {"$gte": cutoff}}, {"created": 1}):
        if ts := _parse_ts(d.get("created")):
            t_created.append(ts)

    # --- Tickets done: status-transition events (from event capture onward) ---
    t_done: list[datetime] = []
    for e in events_collection().find(
        {"kind": "updated", "field": "status", "new": {"$in": sorted(DEFAULT_DONE_STATUSES)}},
        {"ts": 1},
    ):
        ts = e.get("ts")
        if isinstance(ts, datetime):
            t_done.append(ts if ts.tzinfo else ts.replace(tzinfo=UTC))

    series_counts = {
        "prs_opened": _weekly_counts(opened),
        "prs_merged": _weekly_counts(merged),
        "tickets_created": _weekly_counts(t_created),
        "tickets_done": _weekly_counts(t_done),
    }
    for metric, counts in series_counts.items():
        for week in window:
            rows.append({"metric": metric, "week": week, "value": counts.get(week, 0), "n": None})

    # Median is dominated by automation (most PRs merge in minutes via the
    # corrections bot account); p90 surfaces the human tail of the distribution.
    for week in window:
        days = cycle_days.get(week)
        if days:
            rows.append(
                {
                    "metric": "pr_cycle_time_days",
                    "week": week,
                    "value": round(statistics.median(days), 2),
                    "n": len(days),
                }
            )
            p90 = statistics.quantiles(days, n=10)[8] if len(days) >= 2 else days[0]
            rows.append(
                {
                    "metric": "pr_cycle_time_p90_days",
                    "week": week,
                    "value": round(p90, 2),
                    "n": len(days),
                }
            )

    # --- Proactive drift snapshot: open gaps right now, stamped on this week ---
    rows.append(
        {
            "metric": "signals_open",
            "week": window[-1],
            "value": signals_collection().count_documents({"status": "open"}),
            "n": None,
        }
    )

    ops = [
        UpdateOne(
            {"metric": r["metric"], "week": r["week"]},
            {"$set": {**r, "computed_at": now}},
            upsert=True,
        )
        for r in rows
    ]
    if ops:
        ds_metrics_collection().bulk_write(ops, ordered=False)

    per_metric: dict[str, int] = {}
    for r in rows:
        per_metric[r["metric"]] = per_metric.get(r["metric"], 0) + 1
    log.info(
        "metrics computed window=%dw rows=%d %s elapsed=%.1fs",
        weeks,
        len(rows),
        per_metric,
        time.monotonic() - t0,
    )
    return per_metric
