"""Session CRUD + history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from agent_yoku.deps import CurrentUser
from agent_yoku.schemas import CreateSessionResponse, SessionDetail, SessionSummary
from agent_yoku.storage import sessions as sess_mod
from agent_yoku.utils import bson_safe

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionSummary])
async def list_sessions(_user: CurrentUser, limit: int = 25) -> list[SessionSummary]:
    rows = sess_mod.list_sessions(limit=limit)
    return [SessionSummary(**r) for r in rows]


@router.post("", response_model=CreateSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(_user: CurrentUser) -> CreateSessionResponse:
    sid = sess_mod.start_session()
    return CreateSessionResponse(session_id=sid)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, _user: CurrentUser) -> SessionDetail:
    rows = sess_mod.list_sessions(limit=200)
    meta = next((r for r in rows if r["session_id"] == session_id), None)
    if not meta:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    msgs = sess_mod.load_all_messages(session_id)
    return SessionDetail(**meta, messages=[bson_safe(m) for m in msgs])


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_session(session_id: str, _user: CurrentUser) -> None:
    sess_mod.delete_session(session_id)
