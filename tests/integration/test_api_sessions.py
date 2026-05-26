"""API integration tests for sessions + chat (agent mocked)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

pytestmark = pytest.mark.integration


def _tenant() -> str:
    return f"apisess_{uuid.uuid4().hex[:6]}"


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from agent_yoku.config import settings

    MongoClient(settings.mongo_uri).drop_database(f"{settings.mongo_db}_{tenant}")


def _signup(client, tenant: str) -> str:
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": "Tester"},
        json={"email": "tester@example.com", "password": "pw-strong-12345"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


@pytest.fixture
def client(scratch_db):
    from agent_yoku.main import app

    return TestClient(app)


def test_create_then_list_session(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = {"Authorization": f"Bearer {token}"}
        r = client.post("/api/sessions", headers=hdrs)
        assert r.status_code == 201
        sid = r.json()["session_id"]

        r = client.get("/api/sessions", headers=hdrs)
        assert r.status_code == 200
        ids = [s["session_id"] for s in r.json()]
        assert sid in ids
    finally:
        _drop(tenant)


def test_delete_session(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = {"Authorization": f"Bearer {token}"}
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        r = client.delete(f"/api/sessions/{sid}", headers=hdrs)
        assert r.status_code == 204

        r = client.get(f"/api/sessions/{sid}", headers=hdrs)
        assert r.status_code == 404
    finally:
        _drop(tenant)


def test_chat_persists_messages(client, monkeypatch):
    """Mock the agent so we exercise the route without burning OpenAI tokens."""

    class FakeAgent:
        def invoke(self, payload):
            msgs = list(payload["messages"])
            msgs.append(
                AIMessage(
                    content="",
                    tool_calls=[{"id": "c1", "name": "filter_jira", "args": {"status": "open"}}],
                )
            )
            msgs.append(ToolMessage(content="42", tool_call_id="c1", name="filter_jira"))
            msgs.append(AIMessage(content="There are 42 open tickets."))
            return {"messages": msgs}

    from agent_yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = {"Authorization": f"Bearer {token}"}
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        r = client.post(
            "/api/chat",
            headers=hdrs,
            json={"session_id": sid, "query": "how many open tickets?"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "42" in data["answer"]
        assert any(tc["name"] == "filter_jira" for tc in data["tool_calls"])

        detail = client.get(f"/api/sessions/{sid}", headers=hdrs).json()
        assert len(detail["messages"]) >= 4  # human + ai+tool_call + tool + final ai
    finally:
        _drop(tenant)


def test_chat_compact_history_on_followup(client, monkeypatch):
    """The agent should be invoked with prior user + final-AI from turn 1."""

    captured: list = []

    class FakeAgent:
        def invoke(self, payload):
            captured.append(list(payload["messages"]))
            return {
                "messages": [
                    *payload["messages"],
                    AIMessage(content="ack"),
                ]
            }

    from agent_yoku.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", FakeAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = {"Authorization": f"Bearer {token}"}
        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        client.post("/api/chat", headers=hdrs, json={"session_id": sid, "query": "first"})
        client.post("/api/chat", headers=hdrs, json={"session_id": sid, "query": "second"})

        # Turn 2's agent payload should include turn-1 user + ai + the new "second" human.
        msgs2 = captured[1]
        assert len(msgs2) == 3
        assert isinstance(msgs2[0], HumanMessage) and msgs2[0].content == "first"
        assert isinstance(msgs2[1], AIMessage)
        assert isinstance(msgs2[2], HumanMessage) and msgs2[2].content == "second"
    finally:
        _drop(tenant)
