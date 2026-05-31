"""End-to-end session + stats flow through the real FastAPI app.

Drives only endpoints already proven by the existing integration suite, so it's
robust to the storage layer's internal signatures. The agent LLM is mocked so no
network / OpenAI calls happen.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

pytestmark = pytest.mark.integration


@pytest.fixture
def client(scratch_db):
    from yoku.api.main import app

    return TestClient(app)


def _tenant() -> str:
    return f"e2e_{uuid.uuid4().hex[:6]}"


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from yoku.core.config import settings

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


class _FakeAgent:
    """Emits one tool call + final answer, mirroring the real agent shape."""

    def invoke(self, payload, **kwargs):
        msgs = list(payload["messages"])
        msgs.append(
            AIMessage(
                content="",
                tool_calls=[{"id": "c1", "name": "filter", "args": {"source": "jira"}}],
            )
        )
        msgs.append(ToolMessage(content="3", tool_call_id="c1", name="filter"))
        msgs.append(AIMessage(content="There are 3 open tickets."))
        return {"messages": msgs}


@pytest.mark.integration
def test_full_flow_signup_session_chat_stats(client, monkeypatch):
    from yoku.api.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", _FakeAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)

        counts0 = client.get("/api/stats/counts", headers=hdrs).json()
        assert counts0["chat_sessions"] == 0

        sid = client.post("/api/sessions", headers=hdrs).json()["session_id"]

        r = client.post(
            "/api/chat", headers=hdrs, json={"session_id": sid, "query": "open tickets?"}
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "3" in data["answer"]
        assert any(tc["name"] == "filter" for tc in data["tool_calls"])

        counts1 = client.get("/api/stats/counts", headers=hdrs).json()
        assert counts1["chat_sessions"] == 1
        assert counts1["chat_messages"] >= 4

        detail = client.get(f"/api/sessions/{sid}", headers=hdrs).json()
        assert detail["turn_count"] >= 1
        assert len(detail["messages"]) >= 4

        fresh = client.get("/api/stats/freshness", headers=hdrs).json()
        assert isinstance(fresh, list)
        assert all("source" in row and "count" in row for row in fresh)
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_tenant_isolation_end_to_end(client, monkeypatch):
    from yoku.api.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", _FakeAgent())

    t_a, t_b = _tenant(), _tenant()
    try:
        tok_a = _signup(client, t_a, "a@a.com")
        tok_b = _signup(client, t_b, "b@b.com")

        sid_a = client.post("/api/sessions", headers=_auth(tok_a)).json()["session_id"]
        client.post("/api/chat", headers=_auth(tok_a), json={"session_id": sid_a, "query": "hi"})

        counts_b = client.get("/api/stats/counts", headers=_auth(tok_b)).json()
        assert counts_b["chat_sessions"] == 0
        assert counts_b["chat_messages"] == 0

        cross = client.get(f"/api/sessions/{sid_a}", headers=_auth(tok_b))
        assert cross.status_code == 404
    finally:
        _drop(t_a)
        _drop(t_b)


@pytest.mark.integration
def test_chat_unknown_session_404(client, monkeypatch):
    """Posting to a session that was never created must 404, not silently
    auto-create it. Sessions are created explicitly via POST /api/sessions."""
    from yoku.api.routers import chat as chat_route

    monkeypatch.setattr(chat_route, "_AGENT", _FakeAgent())

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.post(
            "/api/chat",
            headers=_auth(token),
            json={"session_id": "never-made", "query": "hi"},
        )
        assert r.status_code == 404, r.text
        detail = client.get("/api/sessions/never-made", headers=_auth(token))
        assert detail.status_code == 404
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_chat_validation_rejects_empty_query(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        sid = client.post("/api/sessions", headers=_auth(token)).json()["session_id"]
        r = client.post("/api/chat", headers=_auth(token), json={"session_id": sid, "query": ""})
        assert r.status_code == 422
    finally:
        _drop(tenant)
