"""Ownership routing — who, exactly, should yoku talk to about a gap.

The chain is deliberately short and stops early (docs/yoku_agent.md Phase 5:
"confident or skip — never guess a person"):

1. The signal's attributed person (assignee / PR author, resolved to a
   unified user at detection time).
2. That person's Slack identity from ds-unified-users (M0a unification).
3. Anything missing → None. The signal stays in the Inbox for humans; a
   `who_knows` expert fallback is deliberately NOT implemented — guessing
   the wrong person costs more trust than staying quiet.
"""

from __future__ import annotations

from yoku.db.mongo import ds_unified_users_collection
from yoku.logging import get_logger

log = get_logger("routing")


def route_target(signal: dict) -> dict | None:
    """Resolve a signal to a DM-able target, or None to skip.

    Returns {"user_id", "slack_user_id", "name", "first_name"}.
    """
    user_id = signal.get("person_user_id")
    if not user_id:
        return None
    doc = ds_unified_users_collection().find_one({"user_id": user_id}, {"_id": 0})
    if not doc or doc.get("is_bot"):
        return None
    slack = doc.get("slack") or {}
    if not slack.get("user_id") or slack.get("is_bot"):
        return None
    name = (
        (doc.get("jira") or {}).get("displayName")
        or slack.get("real_name")
        or slack.get("display_name")
        or (doc.get("github") or {}).get("login")
        or "there"
    )
    return {
        "user_id": user_id,
        "slack_user_id": slack["user_id"],
        "name": name,
        "first_name": name.split()[0] if name else "there",
    }
