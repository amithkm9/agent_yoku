"""Integration tests for the /api/connectors surface.

Covers:
- admin gating (non-admin gets 403, admin gets 200)
- per-tenant isolation (configs in tenant A invisible to tenant B)
- partial updates (blank token keeps existing)
- sync endpoint 400s when not configured
- DELETE removes the config

All tests use scratch tenants and drop the per-tenant mongo db on teardown.
"""

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
    return f"conn_{uuid.uuid4().hex[:6]}"


def _signup(client, tenant, email, password="pw-strong-123") -> tuple[str, dict]:
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": email.split("@")[0]},
        json={"email": email, "password": password},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    return d["access_token"], d["user"]


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from agent_yoku.config import settings

    MongoClient(settings.mongo_uri).drop_database(f"{settings.mongo_db}_{tenant}")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_list_requires_admin(client):
    tenant = _tenant()
    try:
        admin_token, _ = _signup(client, tenant, "admin@x.com")
        # Second signup in same tenant -> not admin.
        user_token, user = _signup(client, tenant, "user@x.com")
        assert user["is_admin"] is False

        assert client.get("/api/connectors", headers=_auth(user_token)).status_code == 403
        r = client.get("/api/connectors", headers=_auth(admin_token))
        assert r.status_code == 200
        names = {c["name"] for c in r.json()}
        assert names == {"jira", "github"}
        # Nothing configured yet.
        for c in r.json():
            assert c["configured"] is False
            assert c["guide"]["display_name"]
            assert isinstance(c["guide"]["setup_steps"], list)
            assert isinstance(c["guide"]["field_help"], dict)
    finally:
        _drop(tenant)


def test_upsert_jira_then_list_shows_no_secret(client):
    tenant = _tenant()
    try:
        token, _ = _signup(client, tenant, "admin@x.com")
        r = client.put(
            "/api/connectors/jira",
            headers=_auth(token),
            json={
                "base_url": "https://acme.atlassian.net",
                "email": "ops@acme.com",
                "token": "super-secret-token",
                "project": "AC",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["configured"] is True
        assert "token" not in r.json()["config"]

        listed = client.get("/api/connectors", headers=_auth(token)).json()
        jira = next(c for c in listed if c["name"] == "jira")
        assert jira["configured"] is True
        assert jira["config"]["project"] == "AC"
        assert "token" not in jira["config"]
        assert jira["guide"]["display_name"] == "JIRA"
    finally:
        _drop(tenant)


def test_blank_token_on_edit_keeps_existing(client):
    tenant = _tenant()
    try:
        token, _ = _signup(client, tenant, "admin@x.com")
        client.put(
            "/api/connectors/jira",
            headers=_auth(token),
            json={
                "base_url": "https://acme.atlassian.net",
                "email": "ops@acme.com",
                "token": "first-token",
                "project": "AC",
            },
        )
        # Edit without a token: should succeed by keeping the previous one.
        r = client.put(
            "/api/connectors/jira",
            headers=_auth(token),
            json={
                "base_url": "https://acme.atlassian.net",
                "email": "ops2@acme.com",
                "project": "AC",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["config"]["email"] == "ops2@acme.com"

        # Verify decrypt still produces the original token by reaching into storage
        # under the same tenant context the API used.
        from agent_yoku.storage import connector_configs as cc
        from agent_yoku.storage import tenancy

        tenancy.set_tenant(tenant)
        assert cc.get_config_decrypted("jira")["token"] == "first-token"
    finally:
        _drop(tenant)


def test_first_time_setup_without_token_rejected(client):
    tenant = _tenant()
    try:
        token, _ = _signup(client, tenant, "admin@x.com")
        r = client.put(
            "/api/connectors/github",
            headers=_auth(token),
            json={"api_base": "https://api.github.com", "org": "acme", "pr_lookback_days": 30},
        )
        assert r.status_code == 400
    finally:
        _drop(tenant)


def test_sync_400_when_not_configured(client):
    tenant = _tenant()
    try:
        token, _ = _signup(client, tenant, "admin@x.com")
        r = client.post("/api/connectors/jira/sync", headers=_auth(token))
        assert r.status_code == 400
    finally:
        _drop(tenant)


def test_delete_clears_config(client):
    tenant = _tenant()
    try:
        token, _ = _signup(client, tenant, "admin@x.com")
        client.put(
            "/api/connectors/github",
            headers=_auth(token),
            json={
                "api_base": "https://api.github.com",
                "org": "acme",
                "token": "gh-tok",
                "pr_lookback_days": 30,
            },
        )
        r = client.delete("/api/connectors/github", headers=_auth(token))
        assert r.status_code == 204

        listed = client.get("/api/connectors", headers=_auth(token)).json()
        gh = next(c for c in listed if c["name"] == "github")
        assert gh["configured"] is False
    finally:
        _drop(tenant)


def test_tenant_isolation_for_configs(client):
    """Configs stored in tenant A are not visible from tenant B."""
    t_a = _tenant()
    t_b = _tenant()
    try:
        token_a, _ = _signup(client, t_a, "admin@a.com")
        token_b, _ = _signup(client, t_b, "admin@b.com")

        client.put(
            "/api/connectors/jira",
            headers=_auth(token_a),
            json={
                "base_url": "https://a.atlassian.net",
                "email": "a@a.com",
                "token": "a-token",
                "project": "AA",
            },
        )
        listed_a = client.get("/api/connectors", headers=_auth(token_a)).json()
        listed_b = client.get("/api/connectors", headers=_auth(token_b)).json()

        assert next(c for c in listed_a if c["name"] == "jira")["configured"] is True
        assert next(c for c in listed_b if c["name"] == "jira")["configured"] is False
    finally:
        _drop(t_a)
        _drop(t_b)


def test_invalid_tenant_id_rejected_at_signup(client):
    r = client.post(
        "/api/auth/signup",
        params={"tenant": "Has Space", "name": "bad"},
        json={"email": "x@x.com", "password": "pw-strong-123"},
    )
    assert r.status_code == 400
