"""Pull Slack workspace members into mongo.

Stored in slack_users — a separate collection from unified_users. The
unify_users pipeline joins slack_users into unified_users on email the same
way it joins github_users.

Usage:
    agent-yoku ingest slack-users
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pymongo import UpdateOne

from agent_yoku.connectors.slack.client import list_users
from agent_yoku.log import get_logger
from agent_yoku.storage.mongo import slack_users_collection

log = get_logger("ingest_slack_users")


def main() -> None:
    log.info("slack users ingest start")
    t0 = time.monotonic()

    coll = slack_users_collection()
    now = datetime.now(UTC)
    ops: list[UpdateOne] = []

    for u in list_users():
        uid = u.get("id")
        if not uid:
            continue
        profile = u.get("profile") or {}
        email = (profile.get("email") or "").lower() or None
        doc = {
            "user_id": uid,
            "name": u.get("name"),
            "display_name": profile.get("display_name") or profile.get("real_name") or u.get("name"),
            "real_name": profile.get("real_name"),
            "email": email,
            "is_bot": bool(u.get("is_bot")),
            "_synced_at": now,
        }
        ops.append(UpdateOne({"user_id": uid}, {"$set": doc}, upsert=True))

    if ops:
        coll.bulk_write(ops, ordered=False)

    elapsed = time.monotonic() - t0
    log.info("slack users done total=%d elapsed=%.1fs", len(ops), elapsed)
