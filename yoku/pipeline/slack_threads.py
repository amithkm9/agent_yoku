"""Roll Slack thread replies up onto their parent message doc.

Slack lands one doc per message, so a threaded discussion is fragmented into
disconnected embeddings — the agent retrieves a reply with no idea what it
answers. This pass rewrites each thread parent's `text` to the whole
discussion (parent + replies in timestamp order), unions the thread's
`jira_keys` onto the parent, and refreshes `reply_count`. The parent's
original single-message text is preserved in `_base_text` so the rollup is
idempotent: re-rolling an unchanged thread is a no-op and keeps its embedding.

Replies stay addressable as their own docs; only the parent carries the
thread-level text/embedding.

Runs as a post-ingest pipeline step (before embed, so changed parents get
re-embedded in the same pass). Usage: `rollup_threads()` under a bound tenant.
"""

from __future__ import annotations

import time

from pymongo import UpdateOne

from yoku.constants import INGEST_BATCH_SIZE
from yoku.db.mongo import dc_slack_collection
from yoku.logging import get_logger

log = get_logger("slack_threads")

_PROJ = {
    "key": 1,
    "channel_id": 1,
    "ts": 1,
    "thread_ts": 1,
    "is_thread_reply": 1,
    "text": 1,
    "_base_text": 1,
    "jira_keys": 1,
}


def _base_text(doc: dict) -> str:
    """The doc's single-message text — pre-roll original if already rolled."""
    return doc.get("_base_text") or doc.get("text") or ""


def _reply_line(doc: dict) -> str:
    """Render a reply as `↳ author: text`, stripping the `[#channel]` tag."""
    body = _base_text(doc)
    if body.startswith("[#"):
        body = body.split("] ", 1)[-1]
    return f"↳ {body}"


def rollup_threads() -> dict[str, int]:
    """Rewrite every thread parent's text to the full discussion.

    Returns counters: threads seen, parents updated, orphan threads skipped
    (parent outside the ingest window).
    """
    t0 = time.monotonic()
    coll = dc_slack_collection()

    replies_by_parent: dict[str, list[dict]] = {}
    for r in coll.find({"is_thread_reply": True}, _PROJ):
        if r.get("channel_id") and r.get("thread_ts"):
            replies_by_parent.setdefault(f"{r['channel_id']}/{r['thread_ts']}", []).append(r)

    updated = 0
    orphans = 0
    ops: list[UpdateOne] = []
    for parent_key, replies in replies_by_parent.items():
        parent = coll.find_one({"key": parent_key}, _PROJ)
        if parent is None:
            orphans += 1
            continue

        replies.sort(key=lambda d: float(d.get("ts") or 0))
        base = _base_text(parent)
        rolled = "\n".join([base, *(_reply_line(r) for r in replies)])
        if rolled == parent.get("text"):
            continue  # thread unchanged — keep text and embedding

        jira_keys = list(parent.get("jira_keys") or [])
        for r in replies:
            for k in r.get("jira_keys") or []:
                if k not in jira_keys:
                    jira_keys.append(k)

        ops.append(
            UpdateOne(
                {"key": parent_key},
                {
                    "$set": {
                        "text": rolled,
                        "_base_text": base,
                        "jira_keys": jira_keys,
                        "reply_count": len(replies),
                        "embedding": None,  # re-embed with the full discussion
                    }
                },
            )
        )
        updated += 1
        if len(ops) >= INGEST_BATCH_SIZE:
            coll.bulk_write(ops, ordered=False)
            ops = []

    if ops:
        coll.bulk_write(ops, ordered=False)

    log.info(
        "slack thread rollup threads=%d updated=%d orphans=%d elapsed=%.1fs",
        len(replies_by_parent),
        updated,
        orphans,
        time.monotonic() - t0,
    )
    return {"threads": len(replies_by_parent), "updated": updated, "orphans": orphans}
