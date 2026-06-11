"""Per-person memory — what keeps yoku from nagging (docs/yoku_agent.md Phase 3).

One doc per person:

    { "user_id": "...",
      "dismissals": [{"pattern": "done_no_pr", "item_key": "jira/AS-1",
                      "note": "...", "by": "<auth user id>", "learned_at": ...}],
      "patterns":  [{"note": "leaves tickets open until QA signs off",
                     "pattern": "done_no_pr"?, "by": ..., "learned_at": ...}],
      "last_interaction_at": ... }

Two writers: Inbox dismissals land here automatically (every human "no" is a
lesson), and the agent's `update_memory` tool records observations from
conversations. The judge consults memory FIRST — it is the cheapest gate.

Suppression rule: one dismissal suppresses only its own item (the signal row
is already sticky); `DISMISS_SUPPRESS_THRESHOLD` dismissals of the same
pattern for the same person suppress that pattern for them entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime

from yoku.db.mongo import person_memory_collection
from yoku.logging import get_logger

log = get_logger("person_memory")

#: This many dismissals of one pattern for one person → stop raising it for them.
DISMISS_SUPPRESS_THRESHOLD = 2


def get_memory(user_id: str) -> dict | None:
    return person_memory_collection().find_one({"user_id": user_id}, {"_id": 0})


def record_dismissal(
    user_id: str,
    pattern: str,
    item_key: str,
    by: str,
    note: str | None = None,
) -> None:
    """A human dismissed a signal about this person — remember it."""
    now = datetime.now(UTC)
    person_memory_collection().update_one(
        {"user_id": user_id},
        {
            "$push": {
                "dismissals": {
                    "pattern": pattern,
                    "item_key": item_key,
                    "note": note,
                    "by": by,
                    "learned_at": now,
                }
            },
            "$set": {"last_interaction_at": now},
        },
        upsert=True,
    )
    log.info("memory: dismissal recorded user=%s pattern=%s item=%s", user_id, pattern, item_key)


def record_pattern(user_id: str, note: str, by: str, pattern: str | None = None) -> None:
    """Record a learned observation about how this person works."""
    now = datetime.now(UTC)
    person_memory_collection().update_one(
        {"user_id": user_id},
        {
            "$push": {"patterns": {"note": note, "pattern": pattern, "by": by, "learned_at": now}},
            "$set": {"last_interaction_at": now},
        },
        upsert=True,
    )


def is_pattern_suppressed(user_id: str | None, pattern: str) -> bool:
    """True when this person has dismissed this pattern often enough that
    raising it again would be nagging."""
    if not user_id:
        return False
    doc = get_memory(user_id)
    if not doc:
        return False
    n = sum(1 for d in doc.get("dismissals") or [] if d.get("pattern") == pattern)
    return n >= DISMISS_SUPPRESS_THRESHOLD
