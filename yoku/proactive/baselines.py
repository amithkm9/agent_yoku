"""Per-person rolling baselines (docs/yoku_agent.md Phase 3).

One doc per unified person, recomputed each sync from the trailing window:

    { "user_id": "...",
      "done_no_pr_rate":      0.92,  # of their done-state tickets, fraction with no PR
      "done_no_pr_n":         13,
      "merged_no_ticket_rate": 0.10, # of their merged PRs, fraction with no ticket ref
      "merged_no_ticket_n":   41,
      "prs_per_week_p50":     3.0,   # weekly authored-PR activity quantiles
      "prs_per_week_p90":     7.0,
      "computed_at": ... }

The two *_rate stats are the person-dimension noise killers: a PM whose done
tickets never have PRs isn't slipping — that's their workflow. The judge
suppresses on high rates deterministically (no LLM), and feeds the rates as
evidence to the LLM person-judgment for the ambiguous middle.
"""

from __future__ import annotations

import statistics
import time
from datetime import UTC, datetime, timedelta

from pymongo import UpdateOne

from yoku.constants import DEFAULT_DONE_STATUSES
from yoku.db.mongo import (
    baselines_collection,
    dc_github_collection,
    dc_jira_collection,
    ds_unified_users_collection,
)
from yoku.logging import get_logger

log = get_logger("baselines")

WINDOW_DAYS = 90


def _monday(dt: datetime) -> str:
    return (dt - timedelta(days=dt.weekday())).date().isoformat()


def compute_baselines(now: datetime | None = None) -> int:
    """Recompute every person's baseline. Returns the number of rows written."""
    now = now or datetime.now(UTC)
    t0 = time.monotonic()
    cutoff = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()

    # Identity maps: source-native names -> unified user_id.
    by_jira_name: dict[str, str] = {}
    by_gh_login: dict[str, str] = {}
    for u in ds_unified_users_collection().find(
        {}, {"user_id": 1, "jira.displayName": 1, "github.login": 1}
    ):
        if name := (u.get("jira") or {}).get("displayName"):
            by_jira_name[name] = u["user_id"]
        if login := (u.get("github") or {}).get("login"):
            by_gh_login[login] = u["user_id"]

    stats: dict[str, dict] = {}

    def _slot(user_id: str) -> dict:
        return stats.setdefault(
            user_id,
            {"done": 0, "done_no_pr": 0, "merged": 0, "merged_no_ticket": 0, "pr_weeks": {}},
        )

    # Ticket dimension: done-state tickets in the window, by assignee.
    for t in dc_jira_collection().find(
        {"status": {"$in": sorted(DEFAULT_DONE_STATUSES)}, "updated": {"$gte": cutoff}},
        {"assignee": 1, "linked_prs": 1},
    ):
        user_id = by_jira_name.get(t.get("assignee") or "")
        if not user_id:
            continue
        s = _slot(user_id)
        s["done"] += 1
        if not t.get("linked_prs"):
            s["done_no_pr"] += 1

    # PR dimension: merged PRs in the window, by author; weekly counts for quantiles.
    for p in dc_github_collection().find(
        {"merged_at": {"$gte": cutoff}}, {"author": 1, "jira_keys": 1, "merged_at": 1}
    ):
        user_id = by_gh_login.get(p.get("author") or "")
        if not user_id:
            continue
        s = _slot(user_id)
        s["merged"] += 1
        if not p.get("jira_keys"):
            s["merged_no_ticket"] += 1
        try:
            week = _monday(datetime.fromisoformat(p["merged_at"].replace("Z", "+00:00")))
            s["pr_weeks"][week] = s["pr_weeks"].get(week, 0) + 1
        except (ValueError, KeyError, TypeError):
            pass

    ops: list[UpdateOne] = []
    for user_id, s in stats.items():
        weekly = sorted(s["pr_weeks"].values())
        doc = {
            "user_id": user_id,
            "done_no_pr_rate": round(s["done_no_pr"] / s["done"], 2) if s["done"] else None,
            "done_no_pr_n": s["done"],
            "merged_no_ticket_rate": (
                round(s["merged_no_ticket"] / s["merged"], 2) if s["merged"] else None
            ),
            "merged_no_ticket_n": s["merged"],
            "prs_per_week_p50": statistics.median(weekly) if weekly else 0,
            "prs_per_week_p90": (
                statistics.quantiles(weekly, n=10)[8]
                if len(weekly) >= 2
                else (weekly[0] if weekly else 0)
            ),
            "computed_at": now,
        }
        ops.append(UpdateOne({"user_id": user_id}, {"$set": doc}, upsert=True))

    if ops:
        baselines_collection().bulk_write(ops, ordered=False)
    log.info("baselines computed people=%d elapsed=%.1fs", len(ops), time.monotonic() - t0)
    return len(ops)


def get_baseline(user_id: str | None) -> dict | None:
    if not user_id:
        return None
    return baselines_collection().find_one({"user_id": user_id}, {"_id": 0})
