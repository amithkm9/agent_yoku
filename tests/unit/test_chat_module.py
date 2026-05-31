"""Unit tests for yoku.agent.chat: ask / final_answer / _extract_text.

The agent + UsageCallback are mocked so no network / OpenAI calls happen.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from yoku.agent import chat as chat_mod


@pytest.mark.unit
def test_extract_text_from_plain_string():
    assert chat_mod._extract_text("hello world") == "hello world"


@pytest.mark.unit
def test_extract_text_from_none():
    assert chat_mod._extract_text(None) == ""


@pytest.mark.unit
def test_extract_text_from_block_list():
    content = [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]
    assert chat_mod._extract_text(content) == "first\n\nsecond"


@pytest.mark.unit
def test_extract_text_from_nested_content_block():
    content = [{"content": [{"type": "text", "text": "deep"}]}]
    assert chat_mod._extract_text(content) == "deep"


@pytest.mark.unit
def test_extract_text_from_bare_string_block():
    assert chat_mod._extract_text(["a", "b"]) == "a\n\nb"


@pytest.mark.unit
def test_extract_text_falls_back_to_str_for_other_types():
    assert chat_mod._extract_text(42) == "42"


@pytest.mark.unit
def test_final_answer_picks_last_toolless_ai_message():
    result = {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[{"id": "1", "name": "filter", "args": {}}]),
            ToolMessage(content="7", tool_call_id="1", name="filter"),
            AIMessage(content="the answer"),
        ]
    }
    assert chat_mod.final_answer(result) == "the answer"


@pytest.mark.unit
def test_final_answer_skips_ai_with_tool_calls():
    result = {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="thinking", tool_calls=[{"id": "1", "name": "t", "args": {}}]),
        ]
    }
    assert chat_mod.final_answer(result) == "<no answer produced>"


@pytest.mark.unit
def test_final_answer_no_messages():
    assert chat_mod.final_answer({}) == "<no answer produced>"


@pytest.mark.unit
def test_ask_invokes_agent_and_returns_state(monkeypatch):
    captured = {}

    class _FakeAgent:
        def invoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            return {"messages": [HumanMessage(content="q"), AIMessage(content="a")]}

    monkeypatch.setattr(chat_mod, "get_agent", lambda: _FakeAgent())

    result = chat_mod.ask("what is up?")
    assert "messages" in result
    msgs = captured["payload"]["messages"]
    assert isinstance(msgs[0], HumanMessage)
    assert msgs[0].content == "what is up?"
    assert "callbacks" in captured["config"]


@pytest.mark.unit
def test_get_agent_is_cached(monkeypatch):
    builds = {"n": 0}

    def _fake_build():
        builds["n"] += 1
        return object()

    monkeypatch.setattr(chat_mod, "build_agent", _fake_build)
    monkeypatch.setattr(chat_mod, "_AGENT", None)

    a = chat_mod.get_agent()
    b = chat_mod.get_agent()
    assert a is b
    assert builds["n"] == 1
    monkeypatch.setattr(chat_mod, "_AGENT", None)
