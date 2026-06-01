"""Extra /api/connectors coverage: slack upsert/sync, 404s, and validation.

Complements test_api_connectors.py. The sync background task's ingest is mocked
so no network calls fire — we only assert the route returns `started`.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def client(scratch_db):
    from yoku.main import app

    return TestClient(app)


def _tenant() -> str:
    return f"connx_{uuid.uuid4().hex[:6]}"


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from yoku.config import settings

    MongoClient(settings.mongo_uri).drop_database(f"{settings.mongo_db}_{tenant}")


def _signup(client, tenant, email="admin@x.com") -> str:
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": email.split("@")[0]},
        json={"email": email, "password": "pw-strong-123"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
def test_upsert_slack_then_list(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.put(
            "/api/connectors/slack",
            headers=_auth(token),
            json={
                "workspace": "acme",
                "bot_token": "xoxb-secret",
                "lookback_days": 30,
                "channel_types": "public_channel",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["configured"] is True
        assert "bot_token" not in r.json()["config"]

        slack = next(
            c
            for c in client.get("/api/connectors", headers=_auth(token)).json()
            if c["name"] == "slack"
        )
        assert slack["config"]["workspace"] == "acme"
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_slack_workspace_url_normalized(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.put(
            "/api/connectors/slack",
            headers=_auth(token),
            json={"workspace": "https://acme.slack.com/", "bot_token": "xoxb-1"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["config"]["workspace"] == "acme"
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_github_org_url_normalized(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.put(
            "/api/connectors/github",
            headers=_auth(token),
            json={"org": "https://github.com/AsatoCorp", "token": "gh-tok"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["config"]["org"] == "AsatoCorp"
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_slack_invalid_channel_types_422(client):
    """A bad channel_types value must yield a clean 422 (not a 500) and a body
    that parses as JSON — the field validator's ValueError must serialize."""
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.put(
            "/api/connectors/slack",
            headers=_auth(token),
            json={
                "workspace": "acme",
                "bot_token": "xoxb-1",
                "channel_types": "bogus_channel",
            },
        )
        assert r.status_code == 422, r.text
        body = r.json()  # must be valid JSON
        assert "error" in body
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_slack_invalid_workspace_422(client):
    """An empty/unparseable workspace must yield a clean 422 JSON body."""
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.put(
            "/api/connectors/slack",
            headers=_auth(token),
            json={"workspace": "   ", "bot_token": "xoxb-1"},
        )
        assert r.status_code == 422, r.text
        assert "error" in r.json()
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_github_invalid_org_422(client):
    """An unparseable GitHub org must yield a clean 422 JSON body."""
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.put(
            "/api/connectors/github",
            headers=_auth(token),
            json={"org": "@@@", "token": "gh-tok"},
        )
        assert r.status_code == 422, r.text
        assert "error" in r.json()
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_delete_unknown_connector_404(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.delete("/api/connectors/bogus", headers=_auth(token))
        assert r.status_code == 404
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_sync_unknown_connector_404(client):
    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        r = client.post("/api/connectors/bogus/sync", headers=_auth(token))
        assert r.status_code == 404
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_sync_started_when_configured(client, monkeypatch):
    # Stub the runner so the background ingest never touches the network.
    from yoku.routers import connectors as conn_route

    ran = {}

    def _fake_runner(tenant_id, cfg):
        ran["tenant"] = tenant_id

    monkeypatch.setitem(
        conn_route._SYNC_FNS, "jira", (_fake_runner, conn_route._SYNC_FNS["jira"][1])
    )

    tenant = _tenant()
    try:
        token = _signup(client, tenant)
        client.put(
            "/api/connectors/jira",
            headers=_auth(token),
            json={
                "base_url": "https://acme.atlassian.net",
                "email": "ops@acme.com",
                "token": "tok",
                "project": "AC",
            },
        )
        r = client.post("/api/connectors/jira/sync", headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started"
    finally:
        _drop(tenant)


@pytest.mark.integration
def test_connectors_requires_auth(client):
    assert client.get("/api/connectors").status_code == 401
