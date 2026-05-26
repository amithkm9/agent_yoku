"""Mongo-backed conversation persistence: sessions + per-turn messages.

Two storage policies:
- *Full fidelity*: every agent message (incl. tool calls + results) is saved
  for UI replay (`load_all_messages`).
- *Compact slice*: when invoking the agent on a follow-up, only user + final
  assistant messages from prior turns are replayed (`load_agent_history`).
  Tool scratchpads are dropped to keep context cost sane.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent_yoku.config import chat_messages_collection, chat_sessions_collection

# =================== SESSIONS ===================


def start_session(title: str | None = None) -> str:
    """Create a new session and return its UUID."""
    sid = str(uuid.uuid4())
    now = datetime.now(UTC)
    chat_sessions_collection().insert_one(
        {
            "session_id": sid,
            "title": title,
            "created_at": now,
            "last_active_at": now,
            "turn_count": 0,
        }
    )
    return sid


def touch_session(
    session_id: str, *, title: str | None = None, increment_turn: bool = False
) -> None:
    now = datetime.now(UTC)
    set_fields: dict = {"last_active_at": now}
    if title:
        set_fields["title"] = title
    update: dict = {
        "$set": set_fields,
        "$setOnInsert": {"session_id": session_id, "created_at": now},
    }
    if increment_turn:
        update["$inc"] = {"turn_count": 1}
    chat_sessions_collection().update_one(
        {"session_id": session_id},
        update,
        upsert=True,
    )


def list_sessions(limit: int = 20) -> list[dict]:
    cursor = (
        chat_sessions_collection()
        .find(
            {},
            {
                "_id": 0,
                "session_id": 1,
                "title": 1,
                "created_at": 1,
                "last_active_at": 1,
                "turn_count": 1,
            },
        )
        .sort("last_active_at", -1)
        .limit(int(limit))
    )
    return list(cursor)


def delete_session(session_id: str) -> None:
    chat_messages_collection().delete_many({"session_id": session_id})
    chat_sessions_collection().delete_one({"session_id": session_id})


# =================== MESSAGES ===================


def _next_turn_seq(session_id: str) -> int:
    last = chat_messages_collection().find_one(
        {"session_id": session_id},
        {"turn_seq": 1},
        sort=[("turn_seq", -1)],
    )
    return (last["turn_seq"] + 1) if last else 1


def _serialize_msg(msg: BaseMessage) -> dict:
    """Flatten a LangChain message to a JSON-friendly mongo doc."""
    out: dict[str, Any] = {
        "role": msg.type,  # human | ai | tool | system
        "content": msg.content,  # str or list[block]
    }
    if msg.type == "ai":
        calls = getattr(msg, "tool_calls", None) or []
        if calls:
            out["tool_calls"] = [
                {"id": c.get("id"), "name": c.get("name"), "args": c.get("args", {})} for c in calls
            ]
    elif msg.type == "tool":
        out["tool_call_id"] = getattr(msg, "tool_call_id", None)
        out["name"] = getattr(msg, "name", None)
    return out


def _deserialize_msg(doc: dict) -> BaseMessage:
    role = doc.get("role")
    content = doc.get("content", "")
    if role == "human":
        return HumanMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    if role == "tool":
        return ToolMessage(
            content=content if isinstance(content, str) else str(content),
            tool_call_id=doc.get("tool_call_id") or "",
            name=doc.get("name"),
        )
    if role == "ai":
        tool_calls = doc.get("tool_calls") or []
        return AIMessage(content=content, tool_calls=tool_calls)
    return HumanMessage(content=str(content))


def save_turn(
    session_id: str,
    turn_id: str,
    messages: list[BaseMessage],
    *,
    user_query: str | None = None,
) -> int:
    """Persist a completed turn's messages. Returns the turn_seq used."""
    if not messages:
        return 0
    turn_seq = _next_turn_seq(session_id)
    now = datetime.now(UTC)
    docs = []
    for idx, m in enumerate(messages):
        docs.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "turn_seq": turn_seq,
                "msg_idx": idx,
                "created_at": now,
                **_serialize_msg(m),
            }
        )
    chat_messages_collection().insert_many(docs)

    # Maintain session counters + auto-title from the first user query.
    title = user_query[:120] if (user_query and turn_seq == 1) else None
    touch_session(session_id, title=title, increment_turn=True)
    return turn_seq


def load_all_messages(session_id: str) -> list[dict]:
    """Raw mongo docs in order — used by the UI to replay the full trace."""
    cursor = (
        chat_messages_collection()
        .find(
            {"session_id": session_id},
            {"_id": 0},
        )
        .sort([("turn_seq", 1), ("msg_idx", 1)])
    )
    return list(cursor)


def load_agent_history(session_id: str) -> list[BaseMessage]:
    """Compact replay for the agent: only user + final-AI message per prior turn.

    Tool calls and intermediate AI messages are dropped — the agent re-plans
    fresh each turn, only needing the prior Q&A as conversational context.
    """
    docs = load_all_messages(session_id)
    if not docs:
        return []

    # Group by turn_seq, then keep human msgs and the LAST ai-without-tool-calls per turn.
    by_turn: dict[int, list[dict]] = {}
    for d in docs:
        by_turn.setdefault(d["turn_seq"], []).append(d)

    out: list[BaseMessage] = []
    for seq in sorted(by_turn):
        turn_docs = by_turn[seq]
        # human messages in order
        for d in turn_docs:
            if d["role"] == "human":
                out.append(_deserialize_msg(d))
        # final answer (last ai without tool_calls)
        final_ai = None
        for d in turn_docs:
            if d["role"] == "ai" and not d.get("tool_calls"):
                final_ai = d
        if final_ai is not None:
            out.append(_deserialize_msg(final_ai))
    return out


def new_turn_id() -> str:
    return str(uuid.uuid4())
