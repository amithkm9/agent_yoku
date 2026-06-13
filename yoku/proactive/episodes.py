"""Episodic memory — the record of what actually happened (Phase A).

yoku already has *semantic* memory (per-person baselines: generalised stats)
and a crude *procedural* memory (dismissal counts). What it has never had is
*episodic* memory: a timestamped, retrievable stream of concrete interactions —
"yoku nudged Priya about AS-4396, she replied X, the gap later healed".

This module is that spine. It writes one `episodes` row per interaction:

    sent       yoku spoke — a real DM or a shadow-mode draft
    reply      the person answered on Slack
    dismissal  a human said "not a gap" in the Inbox
    confirm    a human said "real gap" in the Inbox

Later phases consume this stream: reply-understanding enriches `reply` episodes
(Phase B), consolidation distils episodes into durable beliefs the judge reads
(Phase C), and the reactive agent recalls a person's history (Phase D). Phase A
only *captures* — nothing reads episodes for decisions yet.

Engine-internal: the `episodes` collection stays off the agent's read whitelist,
and writes are best-effort — a memory-write failure must never break a human
action or the speak-first loop. `record_episode` swallows and logs its own
errors and returns None on failure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pymongo import DESCENDING

from yoku.db.mongo import episodes_collection
from yoku.logging import get_logger

log = get_logger("episodes")

# Episode kinds — the four interaction points Phase A captures.
KIND_SENT = "sent"
KIND_REPLY = "reply"
KIND_DISMISSAL = "dismissal"
KIND_CONFIRM = "confirm"
EPISODE_KINDS = frozenset({KIND_SENT, KIND_REPLY, KIND_DISMISSAL, KIND_CONFIRM})

# Where the episode came from / which channel it travelled over.
SOURCE_SLACK = "slack"  # a real DM out or a verified reply in
SOURCE_INBOX = "inbox"  # a human verdict in the web Inbox
SOURCE_ENGINE = "engine"  # an internal event (e.g. a shadow-mode draft)
SOURCE_AGENT = "agent"  # the reactive agent wrote it

# Bound stored text so one chatty reply can't bloat the collection.
_TEXT_MAX = 2000


def record_episode(
    *,
    kind: str,
    text: str,
    person_user_id: str | None = None,
    signal_id: str | None = None,
    item_key: str | None = None,
    detector: str | None = None,
    source: str,
    outcome: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Append one episode to the history. Best-effort — never raises.

    Returns the new episode_id, or None if the write failed (logged) or the
    kind is unknown.

    Args:
        kind: one of EPISODE_KINDS.
        text: human-readable account of what happened (the DM, the reply, or a
            short verdict line). Clipped to `_TEXT_MAX` chars.
        person_user_id: the unified user the episode is about, if known.
        signal_id / item_key / detector: the gap this episode relates to.
        source: SOURCE_* — the channel the episode travelled over.
        outcome: optional qualifier (e.g. "sent" vs "shadow" for a `sent`
            episode, or the verdict label for confirm/dismissal).
        now: injectable clock for tests.
    """
    if kind not in EPISODE_KINDS:
        log.warning("episodes: refusing to record unknown kind %r", kind)
        return None
    episode_id = uuid.uuid4().hex
    doc = {
        "episode_id": episode_id,
        "kind": kind,
        "person_user_id": person_user_id,
        "signal_id": signal_id,
        "item_key": item_key,
        "detector": detector,
        "source": source,
        "outcome": outcome,
        "text": (text or "")[:_TEXT_MAX],
        # Reserved for Phase C — episodes embed for similarity recall there.
        "embedding": None,
        "ts": now or datetime.now(UTC),
    }
    try:
        episodes_collection().insert_one(doc)
    except Exception:
        log.exception("episodes: failed to record %s episode for %s", kind, person_user_id)
        return None
    log.debug(
        "episode %s recorded kind=%s person=%s signal=%s",
        episode_id,
        kind,
        person_user_id,
        signal_id,
    )
    return episode_id


def set_understanding(episode_id: str, understanding: dict) -> None:
    """Attach a reply's parsed understanding to its episode (Phase B).

    Best-effort — a stamping failure must not break the understanding loop.
    """
    try:
        episodes_collection().update_one(
            {"episode_id": episode_id},
            {"$set": {"understanding": understanding}},
        )
    except Exception:
        log.exception("episodes: failed to stamp understanding on %s", episode_id)


def replies_needing_understanding(limit: int = 50) -> list[dict]:
    """Reply episodes not yet parsed by the understanding loop, oldest first.

    A queue mirroring the judge's "unjudged matured signals" pattern, so the
    work drains across syncs under a budget.
    """
    return list(
        episodes_collection()
        .find(
            {"kind": KIND_REPLY, "understanding": {"$exists": False}},
            {"_id": 0, "embedding": 0},
        )
        .sort([("ts", 1)])
        .limit(int(limit))
    )


def get_episodes(
    *,
    person_user_id: str | None = None,
    signal_id: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Read episodes newest-first, optionally scoped to a person, a gap, or
    a set of kinds. The substrate later phases (and tests) read from."""
    query: dict = {}
    if person_user_id:
        query["person_user_id"] = person_user_id
    if signal_id:
        query["signal_id"] = signal_id
    if kinds:
        query["kind"] = {"$in": list(kinds)}
    return list(
        episodes_collection()
        .find(query, {"_id": 0, "embedding": 0})
        .sort([("ts", DESCENDING)])
        .limit(int(limit))
    )
