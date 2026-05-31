"""Integration tests for POST /api/chat/stream (SSE streaming endpoint)."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _tenant() -> str:
    return f"stream_{uuid.uuid4().hex[:6]}"


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from yoku.config import settings

    MongoClient(settings.mongo_uri).drop_database(f"{settings.mongo_db}_{tenant}")


def _signup(client, tenant: str, email: str = "alice@example.com") -> str:
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": email.split("@")[0]},
        json={"email": email, "password": "pw-strong-123"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _parse_sse(raw: bytes) -> list[dict]:
    """Parse raw SSE bytes into a list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in raw.decode().splitlines():
        if line.startswith("event:"):
            current["event"] = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line[len("data:") :].strip())
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# Fake streaming agent
# ---------------------------------------------------------------------------


class FakeStreamAgent:
    """Simulates a LangGraph agent that emits two states:
    1. An AI message with a tool call.
    2. The tool result + final AI answer.
    Exposes .stream() as required by _stream_agent_turn.
    """

    def stream(self, inputs: dict, stream_mode: str = "values", **kwargs):
        prior = list(inputs.get("messages", []))
        human_msg = prior[-1] if prior else HumanMessage(content="?")

        # State snapshot 1: tool call emitted.
        ai_tool_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "tc1",
                    "name": "filter",
                    "args": {"source": "jira", "filters": {"status": "open"}},
                }
            ],
        )
        yield {"messages": prior[:-1] + [human_msg, ai_tool_msg]}

        # State snapshot 2: tool result + final answer.
        tool_result = ToolMessage(content="7", tool_call_id="tc1", name="filter")
        final_ai = AIMessage(content="There are 7 open tickets.")
        yield {"messages": prior[:-1] + [human_msg, ai_tool_msg, tool_result, final_ai]}


class FakeStreamAgentNoTools:
    """Simulates an agent that answers directly with no tool calls."""

    def stream(self, inputs: dict, stream_mode: str = "values", **kwargs):
        prior = list(inputs.get("messages", []))
        human_msg = prior[-1] if prior else HumanMessage(content="?")
        final_ai = AIMessage(content="Direct answer, no tools.")
        yield {"messages": prior[:-1] + [human_msg, final_ai]}


class FakeStreamAgentError:
    """Simulates an agent that raises an exception mid-stream."""

    def stream(self, inputs: dict, stream_mode: str = "values", **kwargs):
        raise RuntimeError("simulated agent failure")
        yield  # make it a generator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client(scratch_db):
    from yoku.main import app

    return TestClient(app)


def test_stream_requires_auth(client):
    r = client.post("/api/chat/stream", json={"session_id": "x", "query": "hi"})
    assert r.status_code == 401


def test_stream_emits_tool_then_answer(client, monkeypatch):
    from yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeStreamAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        r = client.post(
            "/api/chat/stream",
            headers=hdrs,
            json={"session_id": sid, "query": "how many open tickets?"},
        )
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]

        events = _parse_sse(r.content)

        tool_events = [e for e in events if e.get("event") == "tool"]
        answer_events = [e for e in events if e.get("event") == "answer"]

        assert len(tool_events) >= 1, "expected at least one 'tool' SSE event"
        assert tool_events[0]["data"]["name"] == "filter"

        assert len(answer_events) == 1, "expected exactly one 'answer' SSE event"
        answer_data = answer_events[0]["data"]
        assert "7" in answer_data["answer"]
        assert answer_data["session_id"] == sid
        assert "turn_id" in answer_data
    finally:
        _drop(tenant)


def test_stream_answer_contains_tool_calls_summary(client, monkeypatch):
    from yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeStreamAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        r = client.post(
            "/api/chat/stream",
            headers=hdrs,
            json={"session_id": sid, "query": "count tickets"},
        )
        events = _parse_sse(r.content)
        answer = next(e for e in events if e.get("event") == "answer")
        tool_calls = answer["data"].get("tool_calls", [])
        assert any(tc["name"] == "filter" for tc in tool_calls)
    finally:
        _drop(tenant)


def test_stream_no_tools_emits_only_answer(client, monkeypatch):
    from yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeStreamAgentNoTools())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        r = client.post(
            "/api/chat/stream",
            headers=hdrs,
            json={"session_id": sid, "query": "hello"},
        )
        events = _parse_sse(r.content)

        tool_events = [e for e in events if e.get("event") == "tool"]
        answer_events = [e for e in events if e.get("event") == "answer"]

        assert tool_events == [], "expected no tool events"
        assert len(answer_events) == 1
        assert "Direct answer" in answer_events[0]["data"]["answer"]
    finally:
        _drop(tenant)


def test_stream_persists_messages(client, monkeypatch):
    """After streaming completes, messages must be queryable via GET /api/sessions/{id}."""
    from yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeStreamAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        client.post(
            "/api/chat/stream",
            headers=hdrs,
            json={"session_id": sid, "query": "open tickets?"},
        )

        detail = client.get(f"/api/sessions/{sid}", headers=hdrs)
        assert detail.status_code == 200
        msgs = detail.json()["messages"]
        # At minimum: human + ai-with-tool-call + tool-result + final-ai = 4
        assert len(msgs) >= 4, f"expected >= 4 messages, got {len(msgs)}"

        roles = [m["role"] for m in msgs]
        assert "human" in roles
        assert "ai" in roles
    finally:
        _drop(tenant)


def test_stream_turn_count_increments(client, monkeypatch):
    from yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeStreamAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        client.post(
            "/api/chat/stream",
            headers=hdrs,
            json={"session_id": sid, "query": "first question"},
        )

        session = client.get(f"/api/sessions/{sid}", headers=hdrs).json()
        assert session["turn_count"] >= 1
    finally:
        _drop(tenant)


def test_stream_error_emits_error_event(client, monkeypatch):
    from yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeStreamAgentError())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        r = client.post(
            "/api/chat/stream",
            headers=hdrs,
            json={"session_id": sid, "query": "this will fail"},
        )
        assert r.status_code == 200  # SSE: HTTP layer is always 200, error in stream body
        events = _parse_sse(r.content)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) >= 1, "expected an 'error' SSE event"
        assert "detail" in error_events[0]["data"]
    finally:
        _drop(tenant)


def test_stream_tenant_isolation(client, monkeypatch):
    """Messages streamed for tenant A are not visible from tenant B."""
    from yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeStreamAgentNoTools())

    t_a = _tenant()
    t_b = _tenant()
    try:
        token_a = _signup(client, t_a, "alice@a.com")
        token_b = _signup(client, t_b, "bob@b.com")

        sid_a = client.post("/api/sessions", headers=_auth(token_a)).json()["session_id"]

        client.post(
            "/api/chat/stream",
            headers=_auth(token_a),
            json={"session_id": sid_a, "query": "tenant A question"},
        )

        # Tenant B should have zero sessions and can't see tenant A's session.
        sessions_b = client.get("/api/sessions", headers=_auth(token_b)).json()
        assert not any(s["session_id"] == sid_a for s in sessions_b)

        counts_b = client.get("/api/stats/counts", headers=_auth(token_b)).json()
        assert counts_b["chat_sessions"] == 0
    finally:
        _drop(t_a)
        _drop(t_b)
