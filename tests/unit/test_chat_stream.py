"""Streaming chat turn: SSE 'tool' events as tools fire, then a final 'answer'."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent_yoku.routers import chat as chat_mod


class _FakeAgent:
    """Yields full-state snapshots like LangGraph stream_mode='values'."""

    def stream(self, inputs, stream_mode):
        base = inputs["messages"]
        ai_call = AIMessage(
            content="", tool_calls=[{"id": "t1", "name": "semantic_search", "args": {}}]
        )
        yield {"messages": base + [ai_call]}
        tool_msg = ToolMessage(content="results", tool_call_id="t1", name="semantic_search")
        final = AIMessage(content="here is the answer")
        yield {"messages": base + [ai_call, tool_msg, final]}


class _BoomAgent:
    def stream(self, inputs, stream_mode):
        raise RuntimeError("boom")
        yield {}  # pragma: no cover - makes this a generator


def _events(blob: str) -> list[tuple[str, dict]]:
    out = []
    for block in blob.strip().split("\n\n"):
        if not block:
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.mark.unit
def test_emits_tool_then_answer_and_persists(monkeypatch):
    saved = {}
    monkeypatch.setattr(chat_mod.sess_mod, "new_turn_id", lambda: "turn-xyz")
    monkeypatch.setattr(
        chat_mod.sess_mod, "save_turn", lambda *a, **k: saved.update(args=a, kwargs=k)
    )

    blob = "".join(chat_mod._stream_agent_turn("sess-1", "find okta", [], _FakeAgent()))
    events = _events(blob)

    assert ("tool", {"name": "semantic_search"}) in events
    answer_events = [d for ev, d in events if ev == "answer"]
    assert len(answer_events) == 1
    ans = answer_events[0]
    assert ans["answer"] == "here is the answer"
    assert ans["turn_id"] == "turn-xyz"
    assert ans["tool_calls"][0]["name"] == "semantic_search"
    assert saved, "the turn should be persisted before the answer event"


@pytest.mark.unit
def test_emits_error_event_on_failure(monkeypatch):
    blob = "".join(chat_mod._stream_agent_turn("s", "q", [], _BoomAgent()))
    events = _events(blob)
    assert events[-1][0] == "error"
    # generic message — internal exception detail must NOT leak to the client
    assert "boom" not in blob
    assert events[-1][1]["detail"] == "agent turn failed; please retry"


@pytest.mark.unit
def test_sse_format():
    assert chat_mod._sse("tool", {"name": "x"}) == 'event: tool\ndata: {"name": "x"}\n\n'
