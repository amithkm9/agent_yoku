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

_RECENT_DAYS = 14
_MATURATION_DAYS = 3


def _gap_age_days(updated: str | None, now: datetime) -> int:
    if not updated:
        return 0
    try:
        ts = datetime.fromisoformat(updated)
    except ValueError:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0, (now - ts).days)


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
                    "gap_age_days": _gap_age_days(t.get("updated"), now),
                },
                "confidence": 0.7,
                "url": t.get("url"),
            }
        )
    return drafts


DETECTOR = Detector(
    name="done_no_pr",
    kind="drift",
    description="Ticket marked complete with no linked PR — claimed done, no code.",
    maturation_days=_MATURATION_DAYS,
    recent_days=_RECENT_DAYS,
    fn=detect,
)
