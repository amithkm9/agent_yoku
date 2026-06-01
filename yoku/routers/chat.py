"""Chat endpoint — runs the deepagent with persistent session memory."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from yoku.agent.agent import build_agent
from yoku.agent.chat import _extract_text
from yoku.agent.usage import UsageCallback
from yoku.auth import CurrentUser
from yoku.db import sessions as sess_mod
from yoku.logging import get_logger, set_session
from yoku.schemas.api import ChatRequest, ChatResponse, ToolCallSummary

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger("api.chat")

_AGENT = None
_AGENT_LOCK = threading.Lock()


def _agent():
    global _AGENT
    if _AGENT is None:
        with _AGENT_LOCK:
            if _AGENT is None:
                log.info("building deep agent…")
                _AGENT = build_agent()
    return _AGENT


def _summarize_tool_calls(messages: list) -> list[ToolCallSummary]:
    pending: dict[str, ToolCallSummary] = {}
    out: list[ToolCallSummary] = []
    for m in messages:
        if m.type == "ai":
            for c in getattr(m, "tool_calls", None) or []:
                summary = ToolCallSummary(name=c["name"], args=c.get("args", {}))
                pending[c["id"]] = summary
                out.append(summary)
        elif m.type == "tool":
            call = pending.pop(getattr(m, "tool_call_id", None), None)
            if call is not None:
                raw = m.content
                preview = raw if isinstance(raw, str) else json.dumps(raw, default=str)
                call.result_preview = preview[:400]
    return out


def _final_answer(messages: list) -> str:
    """The last AI message with no pending tool calls — the user-facing answer."""
    for m in reversed(messages):
        if m.type == "ai" and not getattr(m, "tool_calls", None):
            return _extract_text(m.content)
    return ""


def _require_session(session_id: str) -> None:
    """Reject chat against a session that doesn't exist for the current tenant.

    Sessions are created explicitly via POST /api/sessions; chatting must not
    silently conjure one. Tenancy is implicit (set by CurrentUser), so this
    existence check is already scoped to the caller's tenant.
    """
    if sess_mod.get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _stream_agent_turn(session_id: str, query: str, prior: list, agent) -> Iterator[str]:
    """Run one turn, emitting an SSE 'tool' event as each tool fires and a final
    'answer' event (after persisting). Streams progress so the UI isn't frozen.

    Uses stream_mode='values' (full state per step) rather than token streaming:
    with deepagents sub-agents, isolating just the final-answer tokens is
    unreliable, whereas tool-activity + final answer is robust and useful.
    """
    try:
        inputs = {"messages": prior + [HumanMessage(content=query)]}
        usage = UsageCallback(session_id=session_id)
        final_msgs = list(prior)
        seen: set[str] = set()
        scanned = 0  # only inspect messages added since the last state snapshot
        for state in agent.stream(inputs, stream_mode="values", config={"callbacks": [usage]}):
            final_msgs = state.get("messages", final_msgs)
            for m in final_msgs[scanned:]:
                if m.type == "ai":
                    for c in getattr(m, "tool_calls", None) or []:
                        if c["id"] not in seen:
                            seen.add(c["id"])
                            yield _sse("tool", {"name": c.get("name")})
            scanned = len(final_msgs)

        usage.log_totals()
        new_msgs = final_msgs[len(prior) :]
        turn_id = sess_mod.new_turn_id()
        sess_mod.save_turn(session_id, turn_id, new_msgs, user_query=query)
        yield _sse(
            "answer",
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "answer": _final_answer(new_msgs),
                "tool_calls": [tc.model_dump() for tc in _summarize_tool_calls(new_msgs)],
            },
        )
    except Exception:  # surface a generic failure; details stay in the server log
        log.exception("chat stream failed for session=%s", session_id)
        yield _sse("error", {"detail": "agent turn failed; please retry"})


@router.post("", response_model=ChatResponse)
async def post_chat(req: ChatRequest, user: CurrentUser) -> ChatResponse:
    set_session(req.session_id)
    _require_session(req.session_id)

    prior = sess_mod.load_agent_history(req.session_id)
    usage = UsageCallback(session_id=req.session_id)
    result = _agent().invoke(
        {"messages": prior + [HumanMessage(content=req.query)]},
        config={"callbacks": [usage]},
    )
    usage.log_totals()
    all_msgs = result.get("messages", [])
    new_msgs = all_msgs[len(prior) :]

    turn_id = sess_mod.new_turn_id()
    sess_mod.save_turn(req.session_id, turn_id, new_msgs, user_query=req.query)

    answer = _final_answer(new_msgs)

    log.info(
        "chat user=%s session=%s tools=%d",
        user.id,
        req.session_id,
        sum(1 for m in new_msgs if m.type == "ai"),
    )

    return ChatResponse(
        session_id=req.session_id,
        turn_id=turn_id,
        answer=answer,
        tool_calls=_summarize_tool_calls(new_msgs),
    )


@router.post("/stream")
async def post_chat_stream(req: ChatRequest, user: CurrentUser) -> StreamingResponse:
    """Same turn as POST /chat, but streamed as Server-Sent Events: a 'tool'
    event per tool call, then a final 'answer' event. The sync generator is run
    in a threadpool by Starlette, so it doesn't block the event loop."""
    set_session(req.session_id)
    _require_session(req.session_id)
    prior = sess_mod.load_agent_history(req.session_id)
    log.info("chat stream user=%s session=%s", user.id, req.session_id)
    return StreamingResponse(
        _stream_agent_turn(req.session_id, req.query, prior, _agent()),
        media_type="text/event-stream",
    )
