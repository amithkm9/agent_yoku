"""Cross-source consistency checks between JIRA tickets and GitHub PRs.

Surfaces the two everyday contradictions in a plan-vs-code corpus:
- tickets marked complete with no linked PR (claimed done, no code), and
- merged PRs with no linked ticket (code shipped, untracked).

Filtering happens in Python (not a mongo query) so the logic is unit-testable
with the in-memory collection doubles; this is a report, not a hot path.
"""

from __future__ import annotations

from yoku.constants import DEFAULT_DONE_STATUSES
from yoku.db.mongo import dc_github_collection, dc_jira_collection


def consistency_report(
    done_statuses: frozenset[str] = DEFAULT_DONE_STATUSES, sample: int = 10
) -> dict:
    """Return counts + sample keys for each cross-source inconsistency."""
    done_without_pr = [
        d["key"]
        for d in dc_jira_collection().find({}, {"_id": 0, "key": 1, "status": 1, "linked_prs": 1})
        if d.get("status") in done_statuses and not d.get("linked_prs")
    ]
    merged_without_ticket = [
        d["key"]
        for d in dc_github_collection().find({}, {"_id": 0, "key": 1, "status": 1, "jira_keys": 1})
        if d.get("status") == "merged" and not d.get("jira_keys")
    ]
    return {
        "done_without_pr": {
            "count": len(done_without_pr),
            "sample": done_without_pr[:sample],
        },
        "merged_without_ticket": {
            "count": len(merged_without_ticket),
            "sample": merged_without_ticket[:sample],
        },
    }
