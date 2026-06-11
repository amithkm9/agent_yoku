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
from yoku.proactive.signals import label_signal
from yoku.schemas.api import InboxResponse, SignalOut

router = APIRouter(prefix="/inbox", tags=["inbox"])

_MAX_SIGNALS = 100


@router.get("", response_model=InboxResponse)
async def list_inbox(_: CurrentUser, limit: int = 50) -> InboxResponse:
    """Matured, unresolved gaps — newest gap first."""
    limit = max(1, min(limit, _MAX_SIGNALS))
    coll = signals_collection()
    matured_filter = {"status": "open", "matured_at": {"$ne": None}}
    rows = list(coll.find(matured_filter, {"_id": 0}).sort([("matured_at", -1)]).limit(limit))
    return InboxResponse(
        signals=[SignalOut(**r) for r in rows],
        total_open=coll.count_documents({"status": "open"}),
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
