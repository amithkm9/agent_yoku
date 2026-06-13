"""Belief consolidation — episodic memory distilled into what yoku knows (Phase C).

Phase A captured episodes; Phase B parsed replies into per-event understanding.
Both are raw history. This phase distils that stream into durable *beliefs*: one
claim per (person, gap-pattern), carrying a confidence yoku can reason with.

A belief is formed from convergent evidence that a pattern is normal for someone:

    explains_as_normal reply   they said so in their own words   (strongest)
    dismissal                  a human marked it not-a-gap
    confirm                    counter-evidence: it WAS a real gap (subtracts)

Decay is modelled as recency-weighting, not a separate decrement pass: each piece
of evidence counts `0.5 ** (age / HALFLIFE)`, so a belief no longer reinforced
fades on its own and the recompute stays idempotent (like `compute_baselines`).
Confidence saturates with weighted evidence; a belief is emitted only above a
floor, so a single stale signal never hardens into a "fact".

Beliefs feed the judge's PERSON dimension as evidence the model weighs — not a
unilateral suppressor. The two-dimension AND-gate still decides; a high-confidence
"this is how they work" simply makes NORMAL the well-supported read. Wholesale
recompute each sync (engine-internal collection), so stale beliefs never linger.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pymongo import UpdateOne

from yoku.db.mongo import beliefs_collection, episodes_collection
from yoku.logging import get_logger
from yoku.proactive.episodes import KIND_CONFIRM, KIND_DISMISSAL, KIND_REPLY

log = get_logger("beliefs")

# Trailing window of episodes to consider, and the recency half-life within it.
WINDOW_DAYS = 180
HALFLIFE_DAYS = 60

# Evidence weights — an explicit explanation outweighs a silent dismissal; a
# confirmation is counter-evidence against "this is normal for them".
W_EXPLAIN = 1.0
W_DISMISS = 0.6
W_CONFIRM = 1.0

# Saturating confidence curve: confidence = net / (net + scale). With scale 1.0,
# net=1 → 0.50, net=3 → 0.75 — diminishing returns so evidence never hits 1.0.
_CONFIDENCE_SCALE = 1.0

# A belief must clear this confidence to be stored — keeps one stale signal from
# hardening into a fact. Tuned so: 1 recent explanation (0.50) or 2 recent
# dismissals (0.55) qualify; a single dismissal (0.38) or decayed evidence do not.
MIN_CONFIDENCE = 0.4

_BELIEF_KINDS = (KIND_REPLY, KIND_DISMISSAL, KIND_CONFIRM)


def _recency(ts: datetime | None, now: datetime) -> float:
    """Exponential recency weight in [0, 1]. Tolerates naive datetimes (pymongo
    reads timestamps back without tzinfo even though we store them UTC-aware)."""
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return 0.5 ** (age_days / HALFLIFE_DAYS)


def _later(a: datetime | None, b: datetime | None) -> datetime | None:
    """The more recent of two timestamps, tolerating either being None."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def consolidate_beliefs(now: datetime | None = None) -> dict[str, int]:
    """Recompute every person's beliefs from the recent episode stream.

    Wholesale replace (engine-internal, idempotent). Returns counters.
    """
    now = now or datetime.now(UTC)
    t0 = time.monotonic()
    cutoff = now - timedelta(days=WINDOW_DAYS)
    run_token = uuid.uuid4().hex

    cursor = episodes_collection().find(
        {
            "ts": {"$gte": cutoff},
            "person_user_id": {"$ne": None},
            "detector": {"$ne": None},
            "kind": {"$in": list(_BELIEF_KINDS)},
        },
        # Only the fields consolidation reads — keeps the cursor light as the
        # episode stream grows (reply `text` can be up to 2 KB each).
        {"_id": 0, "kind": 1, "detector": 1, "person_user_id": 1, "ts": 1, "understanding": 1},
    )

    # (user_id, pattern) -> accumulating evidence.
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"support": 0.0, "against": 0.0, "count": 0, "notes": [], "last": None}
    )
    for ep in cursor:
        kind = ep.get("kind")
        pattern = ep.get("detector")
        uid = ep.get("person_user_id")
        ts = ep.get("ts")
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        r = _recency(ts, now)
        g = groups[(uid, pattern)]

        if kind == KIND_REPLY:
            understanding = ep.get("understanding") or {}
            if understanding.get("outcome") != "explains_as_normal":
                continue  # only explicit "this is normal" replies form beliefs
            g["support"] += W_EXPLAIN * r
            g["count"] += 1
            note = understanding.get("learned_pattern")
            if note:
                g["notes"].append((ts, note))
            g["last"] = _later(g["last"], ts)
        elif kind == KIND_DISMISSAL:
            g["support"] += W_DISMISS * r
            g["count"] += 1
            g["last"] = _later(g["last"], ts)
        elif kind == KIND_CONFIRM:
            g["against"] += W_CONFIRM * r

    docs = []
    for (uid, pattern), g in groups.items():
        net = g["support"] - g["against"]
        if net <= 0 or g["count"] == 0:
            continue
        confidence = round(net / (net + _CONFIDENCE_SCALE), 2)
        if confidence < MIN_CONFIDENCE:
            continue
        notes = [
            n
            for _, n in sorted(
                g["notes"], key=lambda x: x[0] or datetime.min.replace(tzinfo=UTC), reverse=True
            )
        ]
        # Prefer the person's own most-recent words; the fallback stays neutral
        # because the group may mix dismissals and note-less explanations.
        claim = notes[0] if notes else f"{g['count']} prior '{pattern}' signal(s) treated as normal"
        docs.append(
            {
                "user_id": uid,
                "pattern": pattern,
                "kind": "normal_workflow",
                "claim": claim,
                "confidence": confidence,
                "evidence_count": g["count"],
                "last_evidence_at": g["last"],
                "computed_at": now,
                "_run": run_token,
            }
        )

    # Upsert-by-key then drop anything not refreshed this run (decayed below the
    # floor, or its evidence aged out). No global delete, so a concurrent judge /
    # Inbox read never sees an empty collection mid-recompute. `_run` is an exact
    # token, sidestepping mongo's millisecond datetime truncation.
    coll = beliefs_collection()
    if docs:
        coll.bulk_write(
            [
                UpdateOne(
                    {"user_id": d["user_id"], "pattern": d["pattern"]}, {"$set": d}, upsert=True
                )
                for d in docs
            ],
            ordered=False,
        )
    coll.delete_many({"_run": {"$ne": run_token}})

    counts = {"beliefs": len(docs), "people": len({d["user_id"] for d in docs})}
    log.info("beliefs consolidated %s elapsed=%.1fs", counts, time.monotonic() - t0)
    return counts


def get_beliefs(
    user_id: str | None,
    pattern: str | None = None,
    min_confidence: float = 0.0,
) -> list[dict]:
    """Beliefs for a person, highest-confidence first; optionally scoped to one
    gap pattern and/or a confidence floor."""
    if not user_id:
        return []
    query: dict = {"user_id": user_id}
    if pattern:
        query["pattern"] = pattern
    if min_confidence:
        query["confidence"] = {"$gte": min_confidence}
    return list(beliefs_collection().find(query, {"_id": 0}).sort([("confidence", -1)]))
