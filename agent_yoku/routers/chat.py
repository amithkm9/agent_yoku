"""Chat endpoint — runs the deepagent with persistent session memory."""

from __future__ import annotations

import json

from fastapi import APIRouter
from langchain_core.messages import HumanMessage

from agent_yoku.agent.agent import build_agent
from agent_yoku.agent.chat import _extract_text
from agent_yoku.deps import CurrentUser
from agent_yoku.log import get_logger, set_session
from agent_yoku.schemas import ChatRequest, ChatResponse, ToolCallSummary
from agent_yoku.storage import sessions as sess_mod

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger("api.chat")

# Build the agent lazily-and-once per process.
_AGENT = None


def _agent():
    global _AGENT
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


@router.post("", response_model=ChatResponse)
async def post_chat(req: ChatRequest, user: CurrentUser) -> ChatResponse:
    set_session(req.session_id)

    prior = sess_mod.load_agent_history(req.session_id)
    result = _agent().invoke({"messages": prior + [HumanMessage(content=req.query)]})
    all_msgs = result.get("messages", [])
    new_msgs = all_msgs[len(prior) :]

    turn_id = sess_mod.new_turn_id()
    sess_mod.save_turn(req.session_id, turn_id, new_msgs, user_query=req.query)

    answer = ""
    for m in reversed(new_msgs):
        if m.type == "ai" and not getattr(m, "tool_calls", None):
            answer = _extract_text(m.content)
            break

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
