"""Integration tests for /api/stats endpoints."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def client(scratch_db):
    from agent_yoku.main import app

    return TestClient(app)


def _tenant() -> str:
    return f"stats_{uuid.uuid4().hex[:6]}"


def _signup(client, tenant: str, email: str = "alice@example.com") -> str:
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": email.split("@")[0]},
        json={"email": email, "password": "pw-strong-123"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from agent_yoku.config import settings

    MongoClient(settings.mongo_uri).drop_database(f"{settings.mongo_db}_{tenant}")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# /api/stats/counts
# ---------------------------------------------------------------------------


def test_counts_requires_auth(client):
    r = client.get("/api/stats/counts")
    assert r.status_code == 401


def test_counts_returns_expected_keys(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.get("/api/stats/counts", headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        expected_keys = {
            "jira_tickets",
            "jira_users",
            "github_prs",
            "github_users",
            "unified_users",
            "chat_sessions",
            "chat_messages",
        }
        assert expected_keys == set(data.keys())
    finally:
        _drop(tenant)


def test_counts_all_zero_on_fresh_tenant(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.get("/api/stats/counts", headers=_auth(token))
        assert r.status_code == 200
        data = r.json()
        for key, val in data.items():
            assert isinstance(val, int), f"{key} should be int"
            assert val >= 0, f"{key} should be non-negative"
    finally:
        _drop(tenant)


def test_counts_chat_sessions_increments_after_session_create(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)

        before = client.get("/api/stats/counts", headers=hdrs).json()["chat_sessions"]

        client.post("/api/sessions", headers=hdrs)

        after = client.get("/api/stats/counts", headers=hdrs).json()["chat_sessions"]
        assert after == before + 1
    finally:
        _drop(tenant)


def test_counts_multiple_sessions_reflected(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        hdrs = _auth(token)

        for _ in range(3):
            client.post("/api/sessions", headers=hdrs)

        data = client.get("/api/stats/counts", headers=hdrs).json()
        assert data["chat_sessions"] == 3
    finally:
        _drop(tenant)


def test_counts_tenant_isolation(client):
    """Stats for tenant A must not include data from tenant B."""
    t_a = _tenant()
    t_b = _tenant()
    try:
        token_a = _signup(client, t_a, "alice@a.com")
        token_b = _signup(client, t_b, "bob@b.com")

        # Tenant A creates 2 sessions, tenant B creates 1.
        client.post("/api/sessions", headers=_auth(token_a))
        client.post("/api/sessions", headers=_auth(token_a))
        client.post("/api/sessions", headers=_auth(token_b))

        counts_a = client.get("/api/stats/counts", headers=_auth(token_a)).json()
        counts_b = client.get("/api/stats/counts", headers=_auth(token_b)).json()

        assert counts_a["chat_sessions"] == 2
        assert counts_b["chat_sessions"] == 1
    finally:
        _drop(t_a)
        _drop(t_b)


# ---------------------------------------------------------------------------
# /api/stats/freshness
# ---------------------------------------------------------------------------


def test_freshness_requires_auth(client):
    r = client.get("/api/stats/freshness")
    assert r.status_code == 401


def test_freshness_returns_list(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.get("/api/stats/freshness", headers=_auth(token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)
    finally:
        _drop(tenant)


def test_freshness_items_have_required_fields(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        rows = client.get("/api/stats/freshness", headers=_auth(token)).json()
        for row in rows:
            assert "source" in row, "missing 'source' field"
            assert "count" in row, "missing 'count' field"
            assert isinstance(row["count"], int)
    finally:
        _drop(tenant)


def test_freshness_tenant_isolation(client):
    """Freshness data from tenant A doesn't bleed into tenant B."""
    t_a = _tenant()
    t_b = _tenant()
    try:
        token_a = _signup(client, t_a, "alice@a.com")
        token_b = _signup(client, t_b, "bob@b.com")

        rows_a = client.get("/api/stats/freshness", headers=_auth(token_a)).json()
        rows_b = client.get("/api/stats/freshness", headers=_auth(token_b)).json()

        # Both should be valid lists; counts in each tenant should be independent.
        assert isinstance(rows_a, list)
        assert isinstance(rows_b, list)

        counts_a = {row["source"]: row["count"] for row in rows_a}
        counts_b = {row["source"]: row["count"] for row in rows_b}

        # Fresh tenants — no data imported, all counts must be zero.
        for source, count in counts_a.items():
            assert count == 0, f"tenant A {source} count should be 0, got {count}"
        for source, count in counts_b.items():
            assert count == 0, f"tenant B {source} count should be 0, got {count}"
    finally:
        _drop(t_a)
        _drop(t_b)
