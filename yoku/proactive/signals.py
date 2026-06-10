"""Signal lifecycle — detectors emit drafts, this module owns the state machine.

One row per (detector, item_key), upserted — never duplicated:

  open       gap currently observed; surfaced in the Inbox once matured
  dismissed  a human said "not a real gap" — STICKY: re-detection never reopens
  resolved   the gap stopped being observed (resolution: self_healed) or was
             dismissed; a later re-occurrence of a self-healed gap reopens fresh

Maturation (hysteresis): a signal surfaces only when the gap has persisted —
either the source data says it is already old (`evidence.gap_age_days`) or it
has stayed open across syncs (`first_seen_at`). Transient states never reach
the Inbox.

Outcomes are the ground truth for Phase 3 memory and the precision/recall
eval (docs/yoku_agent.md §7.3): every dismissal/confirmation is a label, every
self-heal is a "nudge would have been pointless" datapoint. Capture them all.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from yoku.db.mongo import ds_unified_users_collection, signals_collection
from yoku.logging import get_logger
from yoku.proactive.detectors import discover_detectors

log = get_logger("signals")


def _resolve_person(query: dict | None, cache: dict) -> tuple[str | None, str | None]:
    """ds-unified-users lookup -> (user_id, display name). Cached per run."""
    if not query:
        return None, None
    key = tuple(sorted(query.items()))
    if key in cache:
        return cache[key]
    u = ds_unified_users_collection().find_one(query, {"_id": 0, "embedding": 0})
    if u is None:
        result = (None, None)
    else:
        name = (
            (u.get("jira") or {}).get("displayName")
            or (u.get("github") or {}).get("name")
            or (u.get("github") or {}).get("login")
            or (u.get("slack") or {}).get("display_name")
            or u.get("email")
        )
        result = (u.get("user_id"), name)
    cache[key] = result
    return result


def run_detectors(now: datetime | None = None) -> dict[str, dict]:
    """Run every detector, reconcile drafts against stored signals.

    Per detector: upsert fresh drafts, mature persistent gaps, auto-resolve
    gaps no longer observed. Returns per-detector counters.
    """
    now = now or datetime.now(UTC)
    t0 = time.monotonic()
    coll = signals_collection()
    cache: dict = {}
    stats: dict[str, dict] = {}

    for det in discover_detectors():
        drafts = det.fn()
        found_keys: set[str] = set()
        counts = {"found": len(drafts), "new": 0, "reopened": 0, "self_healed": 0}

        for d in drafts:
            item_key = d["item_key"]
            found_keys.add(item_key)
            user_id, person_name = _resolve_person(d.get("person_query"), cache)
            gap_age = int((d.get("evidence") or {}).get("gap_age_days") or 0)
            already_mature = gap_age >= det.maturation_days
            fresh = {
                "title": d.get("title"),
                "person_user_id": user_id,
                "person_name": person_name or d.get("person_name_raw"),
                "evidence": d.get("evidence") or {},
                "confidence": d.get("confidence", 0.5),
                "url": d.get("url"),
                "last_seen_at": now,
            }

            existing = coll.find_one(
                {"detector": det.name, "item_key": item_key},
                {"status": 1, "matured_at": 1},
            )
            if existing is None:
                coll.insert_one(
                    {
                        "signal_id": uuid.uuid4().hex,
                        "detector": det.name,
                        "kind": det.kind,
                        "item_key": item_key,
                        **fresh,
                        "status": "open",
                        "label": None,
                        "resolution": None,
                        "first_seen_at": now,
                        "matured_at": now if already_mature else None,
                        "created_at": now,
                    }
                )
                counts["new"] += 1
            elif existing["status"] == "dismissed":
                # Sticky: a human said no. Track that we still see it, nothing more.
                coll.update_one({"_id": existing["_id"]}, {"$set": {"last_seen_at": now}})
            elif existing["status"] == "resolved":
                # Self-healed gap re-opened — treat as a fresh occurrence.
                coll.update_one(
                    {"_id": existing["_id"]},
                    {
                        "$set": {
                            **fresh,
                            "status": "open",
                            "resolution": None,
                            "first_seen_at": now,
                            "matured_at": now if already_mature else None,
                        }
                    },
                )
                counts["reopened"] += 1
            else:  # open — refresh evidence, maybe mature
                update = dict(fresh)
                if existing.get("matured_at") is None and already_mature:
                    update["matured_at"] = now
                coll.update_one({"_id": existing["_id"]}, {"$set": update})

        # Mature anything that has stayed open across the window.
        coll.update_many(
            {
                "detector": det.name,
                "status": "open",
                "matured_at": None,
                "first_seen_at": {"$lte": now - timedelta(days=det.maturation_days)},
            },
            {"$set": {"matured_at": now}},
        )
        # Auto-resolve open signals whose gap is no longer observed.
        healed = coll.update_many(
            {"detector": det.name, "status": "open", "item_key": {"$nin": sorted(found_keys)}},
            {"$set": {"status": "resolved", "resolution": "self_healed", "resolved_at": now}},
        )
        counts["self_healed"] = healed.modified_count
        stats[det.name] = counts

    log.info("detectors done %s elapsed=%.1fs", stats, time.monotonic() - t0)
    return stats


def label_signal(signal_id: str, label: str, user_id: str) -> dict | None:
    """Record a human verdict on a signal — the eval label stream.

    confirm: "real gap" — stays open/actionable, labeled for precision metrics.
    dismiss: "not a gap" — sticky; the detector will never resurface this item.
    Returns the updated signal, or None if the id is unknown.
    """
    now = datetime.now(UTC)
    update: dict = {"label": label, "labeled_by": user_id, "labeled_at": now}
    if label == "dismissed":
        update["status"] = "dismissed"
        update["resolution"] = "dismissed"
    coll = signals_collection()
    coll.update_one({"signal_id": signal_id}, {"$set": update})
    return coll.find_one({"signal_id": signal_id}, {"_id": 0})
