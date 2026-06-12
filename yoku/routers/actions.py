"""Write-back approval API (Phase 6) — the human side of the proposal gate.

- GET  /api/actions                pending (and recent settled) proposals
- POST /api/actions/{id}/approve   execute under tenant credentials (admin)
- POST /api/actions/{id}/reject    record the no (admin)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from yoku.actions import executor
from yoku.auth import AdminUser, CurrentUser
from yoku.db.mongo import action_log_collection
from yoku.schemas.api import ActionOut, ActionsResponse

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("", response_model=ActionsResponse)
async def list_actions(_: CurrentUser, limit: int = 50) -> ActionsResponse:
    limit = max(1, min(limit, 100))
    coll = action_log_collection()
    rows = list(coll.find({}, {"_id": 0}).sort([("proposed_at", -1)]).limit(limit))
    return ActionsResponse(
        actions=[ActionOut(**r) for r in rows],
        total_proposed=coll.count_documents({"status": "proposed"}),
    )


@router.post("/{action_id}/approve", response_model=ActionOut)
async def approve_action(action_id: str, admin: AdminUser) -> ActionOut:
    """Execute a proposed action. Admin-only — this touches external systems."""
    try:
        row = executor.execute(action_id, approved_by=admin.id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except Exception as e:
        # Execution failed — the audit row already records it; surface why.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"action failed: {type(e).__name__}: {e}"
        ) from e
    return ActionOut(**row)


@router.post("/{action_id}/reject", response_model=ActionOut)
async def reject_action(action_id: str, admin: AdminUser) -> ActionOut:
    try:
        row = executor.reject(action_id, rejected_by=admin.id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return ActionOut(**row)
