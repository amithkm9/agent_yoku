"""Integration: persist a multi-turn session to mongo and reconstruct it.

Skips if local mongo isn't running.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_session(scratch_db, monkeypatch):
    """Patch the session collection helpers to point at the scratch db."""
    from yoku.db import mongo as mongo_mod
    from yoku.db import sessions as sess_mod

    def _msgs():
        coll = scratch_db["chat_messages"]
        coll.create_index([("session_id", 1), ("turn_seq", 1), ("msg_idx", 1)])
        return coll

    def _sess():
        coll = scratch_db["chat_sessions"]
        coll.create_index("session_id", unique=True)
        return coll

    monkeypatch.setattr(mongo_mod, "chat_messages_collection", _msgs)
    monkeypatch.setattr(mongo_mod, "chat_sessions_collection", _sess)
    monkeypatch.setattr(sess_mod, "chat_messages_collection", _msgs)
    monkeypatch.setattr(sess_mod, "chat_sessions_collection", _sess)
    yield


def test_save_then_load_history(temp_session):
    from yoku.db.sessions import (
        load_agent_history,
        load_all_messages,
        new_turn_id,
        save_turn,
        start_session,
    )

    sid = start_session()
    # turn 1
    save_turn(
        sid,
        new_turn_id(),
        [
            HumanMessage(content="hi"),
            AIMessage(content="hello back"),
        ],
        user_query="hi",
    )
    # turn 2 — agent does tool call then answers
    save_turn(
        sid,
        new_turn_id(),
        [
            HumanMessage(content="get AS-1"),
            AIMessage(
                content="", tool_calls=[{"id": "c1", "name": "get", "args": {"key": "AS-1"}}]
            ),
            ToolMessage(content='{"key":"AS-1"}', tool_call_id="c1", name="get"),
            AIMessage(content="here is AS-1"),
        ],
    )

    # Full replay = every message persisted (used by the UI trace).
    all_msgs = load_all_messages(sid)
    assert len(all_msgs) == 6

    # Compact history = user + final-AI per turn (fed to the next agent run).
    compact = load_agent_history(sid)
    assert len(compact) == 4
    assert compact[0].type == "human" and compact[0].content == "hi"
    assert compact[1].type == "ai" and compact[1].content == "hello back"
    assert compact[2].type == "human" and "get AS-1" in compact[2].content
    assert compact[3].type == "ai" and "here is AS-1" in compact[3].content
