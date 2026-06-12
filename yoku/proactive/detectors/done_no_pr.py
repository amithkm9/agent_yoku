"""Ticket claims work is finished but no PR ever linked to it.

Generalizes the done-without-PR half of `pipeline/consistency.py` into a
signal-emitting detector. Scans recently-touched tickets only — ancient
history is not actionable and would flood the Inbox.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from yoku.constants import DEFAULT_DONE_STATUSES
from yoku.db.mongo import dc_jira_collection
from yoku.proactive.detectors._base import Detector
from yoku.utils.dates import days_since

_RECENT_DAYS = 14
_MATURATION_DAYS = 3


def detect() -> list[dict]:
    now = datetime.now(UTC)
    cutoff = (now - timedelta(days=_RECENT_DAYS)).strftime("%Y-%m-%d")
    drafts: list[dict] = []
    cursor = dc_jira_collection().find(
        {"status": {"$in": sorted(DEFAULT_DONE_STATUSES)}, "updated": {"$gte": cutoff}},
        {
            "_id": 0,
            "key": 1,
            "summary": 1,
            "status": 1,
            "assignee": 1,
            "linked_prs": 1,
            "updated": 1,
            "url": 1,
        },
    )
    for t in cursor:
        if t.get("linked_prs"):
            continue
        assignee = t.get("assignee")
        drafts.append(
            {
                "item_key": f"jira/{t['key']}",
                "title": t.get("summary"),
                "person_query": {"jira.displayName": assignee} if assignee else None,
                "person_name_raw": assignee,
                "evidence": {
                    "status": t.get("status"),
                    "updated": t.get("updated"),
                    "gap_age_days": days_since(t.get("updated"), now),
                },
                "confidence": 0.7,
                "url": t.get("url"),
            }
        )
    return drafts


def compose(signal: dict, target: dict) -> str:
    key = signal["item_key"].removeprefix("jira/")
    title = (signal.get("title") or "").strip()
    if len(title) > 80:
        title = title[:79].rstrip() + "…"
    quoted = f' ("{title}")' if title else ""
    status = (signal.get("evidence") or {}).get("status") or "done"
    return (
        f"Hey {target['first_name']} — {key}{quoted} is marked {status}, but I "
        f"can't find a PR linked to it. Did the code ship somewhere I'm not seeing?"
    )


DETECTOR = Detector(
    name="done_no_pr",
    kind="drift",
    label="Done, no PR",
    description="Ticket marked complete with no linked PR — claimed done, no code.",
    gap_description="their ticket is done-state with no linked PR",
    maturation_days=_MATURATION_DAYS,
    recent_days=_RECENT_DAYS,
    fn=detect,
    baseline_rate_field="done_no_pr_rate",
    baseline_n_field="done_no_pr_n",
    compose=compose,
)
