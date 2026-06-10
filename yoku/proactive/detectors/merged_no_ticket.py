"""PR merged with no JIRA ticket referenced — code shipped, untracked.

The other half of `pipeline/consistency.py`, as a signal-emitting detector.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from yoku.db.mongo import dc_github_collection
from yoku.proactive.detectors._base import Detector
from yoku.proactive.detectors.done_no_pr import _gap_age_days

_RECENT_DAYS = 14
_MATURATION_DAYS = 3


def detect() -> list[dict]:
    now = datetime.now(UTC)
    cutoff = (now - timedelta(days=_RECENT_DAYS)).strftime("%Y-%m-%d")
    drafts: list[dict] = []
    cursor = dc_github_collection().find(
        {"status": "merged", "jira_keys": [], "updated": {"$gte": cutoff}},
        {"_id": 0, "key": 1, "summary": 1, "author": 1, "repo": 1, "updated": 1, "url": 1},
    )
    for pr in cursor:
        author = pr.get("author")
        drafts.append(
            {
                "item_key": f"github/{pr['key']}",
                "title": pr.get("summary"),
                "person_query": {"github.login": author} if author else None,
                "person_name_raw": author,
                "evidence": {
                    "repo": pr.get("repo"),
                    "updated": pr.get("updated"),
                    "gap_age_days": _gap_age_days(pr.get("updated"), now),
                },
                "confidence": 0.7,
                "url": pr.get("url"),
            }
        )
    return drafts


DETECTOR = Detector(
    name="merged_no_ticket",
    kind="drift",
    description="Merged PR with no JIRA ticket referenced — code shipped, untracked.",
    maturation_days=_MATURATION_DAYS,
    recent_days=_RECENT_DAYS,
    fn=detect,
)
