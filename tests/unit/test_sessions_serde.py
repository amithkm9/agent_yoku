"""Round-trip serialization tests for chat messages."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from yoku.storage.sessions import _deserialize_msg, _serialize_msg


def test_human_roundtrip():
    msg = HumanMessage(content="hello")
    rebuilt = _deserialize_msg(_serialize_msg(msg))
    assert rebuilt.type == "human"
    assert rebuilt.content == "hello"


def test_ai_with_tool_calls_roundtrip():
    msg = AIMessage(
        content="",
        tool_calls=[{"id": "c1", "name": "get", "args": {"key": "x"}}],
    )
    out = _serialize_msg(msg)
    assert out["tool_calls"][0]["name"] == "get"
    rebuilt = _deserialize_msg(out)
    assert rebuilt.type == "ai"
    assert rebuilt.tool_calls[0]["name"] == "get"


def test_tool_roundtrip():
    msg = ToolMessage(content="result", tool_call_id="c1", name="get")
    out = _serialize_msg(msg)
    assert out["tool_call_id"] == "c1"
    rebuilt = _deserialize_msg(out)
    assert rebuilt.type == "tool"
    assert rebuilt.tool_call_id == "c1"
