"""PR merged with no JIRA ticket referenced — code shipped, untracked.

The other half of `pipeline/consistency.py`, as a signal-emitting detector.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from yoku.db.mongo import dc_github_collection
from yoku.proactive.detectors._base import Detector
from yoku.utils.dates import days_since

_RECENT_DAYS = 14
_MATURATION_DAYS = 3


def detect() -> list[dict]:
    now = datetime.now(UTC)
    cutoff = (now - timedelta(days=_RECENT_DAYS)).strftime("%Y-%m-%d")
    drafts: list[dict] = []
    cursor = dc_github_collection().find(
        # `$in [[], None]` + missing-field: ingest always writes jira_keys, but
        # docs from older ingest versions may lack it — same "no ticket"
        # semantics as consistency.py's `not d.get("jira_keys")`.
        {
            "status": "merged",
            "updated": {"$gte": cutoff},
            "$or": [{"jira_keys": {"$in": [[], None]}}, {"jira_keys": {"$exists": False}}],
        },
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
                    "gap_age_days": days_since(pr.get("updated"), now),
                },
                "confidence": 0.7,
                "url": pr.get("url"),
            }
        )
    return drafts


def compose(signal: dict, target: dict) -> str:
    key = signal["item_key"].removeprefix("github/")
    title = (signal.get("title") or "").strip()
    if len(title) > 80:
        title = title[:79].rstrip() + "…"
    quoted = f' ("{title}")' if title else ""
    return (
        f"Hey {target['first_name']} — {key}{quoted} merged without a ticket "
        f"reference. Is there a ticket I should link it to, or was this untracked work?"
    )


DETECTOR = Detector(
    name="merged_no_ticket",
    kind="drift",
    label="Merged, no ticket",
    description="Merged PR with no JIRA ticket referenced — code shipped, untracked.",
    gap_description="their PR merged with no ticket referenced",
    maturation_days=_MATURATION_DAYS,
    recent_days=_RECENT_DAYS,
    fn=detect,
    baseline_rate_field="merged_no_ticket_rate",
    baseline_n_field="merged_no_ticket_n",
    compose=compose,
)
