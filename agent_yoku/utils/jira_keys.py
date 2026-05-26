"""JIRA ticket key extraction.

The `AS-XXXX` regex links cross-source artifacts (PR branches/titles/bodies,
Slack messages, Notion pages, commit messages, …) back to JIRA tickets.
"""

from __future__ import annotations

import re

JIRA_KEY_RE = re.compile(r"\bAS-\d+\b")


def extract_jira_keys(*sources: str | None) -> list[str]:
    """Scan every input string for `AS-XXXX` matches; return dedup'd in order."""
    seen: dict[str, None] = {}
    for s in sources:
        if not s:
            continue
        for m in JIRA_KEY_RE.findall(s):
            seen.setdefault(m, None)
    return list(seen.keys())
