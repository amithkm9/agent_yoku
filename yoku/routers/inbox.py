"""Proactive Inbox API — matured gap signals + human verdicts.

Endpoints (under /api/inbox, tenant-scoped via the auth dependency):

- GET  /                      matured open signals, newest first
- POST /{signal_id}/confirm   label: "this is a real gap"   (eval label)
- POST /{signal_id}/dismiss   label + sticky suppression    (eval label)

Confirm/dismiss clicks are the labeled dataset behind the precision metric
(docs/yoku_agent.md §7.3) — the Inbox doubles as the labeling tool.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from yoku.auth import CurrentUser
from yoku.db.mongo import signals_collection
from yoku.proactive.beliefs import get_beliefs
from yoku.proactive.signals import label_signal
from yoku.schemas.api import InboxResponse, SignalOut

router = APIRouter(prefix="/inbox", tags=["inbox"])

_MAX_SIGNALS = 100


def _person_beliefs(row: dict, cache: dict) -> list[dict]:
    """Beliefs about this signal's person, relevant to its gap type — the
    memory context behind why yoku judged (or stayed quiet about) the gap.
    Cached per (person, detector) so a busy Inbox is a handful of reads."""
    uid = row.get("person_user_id")
    if not uid:
        return []
    key = (uid, row.get("detector"))
    if key not in cache:
        cache[key] = [
            {
                "claim": b["claim"],
                "confidence": b["confidence"],
                "evidence_count": b["evidence_count"],
            }
            for b in get_beliefs(uid, pattern=row.get("detector"))
        ]
    return cache[key]


@router.get("", response_model=InboxResponse)
async def list_inbox(_: CurrentUser, limit: int = 50) -> InboxResponse:
    """Matured, unresolved gaps — including drafted (shadow) and sent DMs.

    Shadow signals are the human-review surface for the speak-first loop:
    each carries the exact message yoku would send, any commitment the person
    made, and what yoku has learned about how they work.
    """
    limit = max(1, min(limit, _MAX_SIGNALS))
    coll = signals_collection()
    active = {"$in": ["open", "shadow", "sent"]}
    matured_filter = {"status": active, "matured_at": {"$ne": None}}
    rows = list(coll.find(matured_filter, {"_id": 0}).sort([("matured_at", -1)]).limit(limit))
    belief_cache: dict = {}
    return InboxResponse(
        signals=[SignalOut(person_beliefs=_person_beliefs(r, belief_cache), **r) for r in rows],
        total_open=coll.count_documents({"status": active}),
        total_matured=coll.count_documents(matured_filter),
        total_suppressed=coll.count_documents({"status": "judged"}),
    )


def _label(signal_id: str, label: str, user_id: str) -> SignalOut:
    doc = label_signal(signal_id, label, user_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown signal {signal_id!r}")
    return SignalOut(**doc)


@router.post("/{signal_id}/confirm", response_model=SignalOut)
async def confirm_signal(signal_id: str, user: CurrentUser) -> SignalOut:
    """Human verdict: real gap. Stays actionable; recorded as a precision label."""
    return _label(signal_id, "confirmed", user.id)


@router.post("/{signal_id}/dismiss", response_model=SignalOut)
async def dismiss_signal(signal_id: str, user: CurrentUser) -> SignalOut:
    """Human verdict: not a gap. Sticky — this item never resurfaces."""
    return _label(signal_id, "dismissed", user.id)
