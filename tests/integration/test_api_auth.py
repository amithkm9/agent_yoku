"""API integration tests — sign-up, login, JWT, tenant isolation."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def client(scratch_db):
    """TestClient bound to a scratch tenant — drops the db on teardown.

    Uses the existing scratch_db fixture's name as the tenant_id so collections
    land in `yoku_<scratch_db_name>` and get cleaned up by that fixture.
    """
    from yoku.main import app

    return TestClient(app)


def _tenant() -> str:
    return f"apitest_{uuid.uuid4().hex[:6]}"


def _signup(
    client, tenant, email="alice@example.com", password="pw-strong-123"
) -> tuple[str, dict]:
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": "Alice"},
        json={"email": email, "password": password},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    return d["access_token"], d["user"]


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from yoku.config import settings

    MongoClient(settings.mongo_uri).drop_database(f"{settings.mongo_db}_{tenant}")


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_signup_then_me(client):
    tenant = _tenant()
    try:
        token, user = _signup(client, tenant)
        assert user["is_admin"] is True
        assert user["tenant_id"] == tenant
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["email"] == "alice@example.com"
    finally:
        _drop(tenant)


def test_login_wrong_password_401(client):
    tenant = _tenant()
    try:
        _signup(client, tenant)
        r = client.post(
            "/api/auth/login-json",
            params={"tenant": tenant},
            json={"email": "alice@example.com", "password": "nope"},
        )
        assert r.status_code == 401
    finally:
        _drop(tenant)


def test_login_requires_explicit_tenant(client):
    r = client.post(
        "/api/auth/login-json",
        json={"email": "alice@example.com", "password": "pw-strong-123"},
    )
    assert r.status_code == 422


def test_duplicate_signup_409(client):
    tenant = _tenant()
    try:
        _signup(client, tenant)
        r = client.post(
            "/api/auth/signup",
            params={"tenant": tenant},
            json={"email": "alice@example.com", "password": "pw-strong-123"},
        )
        assert r.status_code == 409
    finally:
        _drop(tenant)


def test_signup_requires_explicit_tenant(client):
    r = client.post(
        "/api/auth/signup",
        params={"name": "Alice"},
        json={"email": "alice@example.com", "password": "pw-strong-123"},
    )
    assert r.status_code == 422


def test_me_without_token_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_tenant_isolation(client):
    """Two tenants — alice in tenant A can't see bob's data in tenant B."""
    t_a = _tenant()
    t_b = _tenant()
    try:
        token_a, _ = _signup(client, t_a, email="alice@x.com")
        token_b, _ = _signup(client, t_b, email="bob@x.com")

        # Each user creates one session in their own tenant.
        r = client.post("/api/sessions", headers={"Authorization": f"Bearer {token_a}"})
        assert r.status_code == 201
        client.post("/api/sessions", headers={"Authorization": f"Bearer {token_b}"})

        # Alice's stats should show 1 session in HER db, not 2.
        s_a = client.get("/api/stats/counts", headers={"Authorization": f"Bearer {token_a}"}).json()
        s_b = client.get("/api/stats/counts", headers={"Authorization": f"Bearer {token_b}"}).json()
        assert s_a["chat_sessions"] == 1
        assert s_b["chat_sessions"] == 1
    finally:
        _drop(t_a)
        _drop(t_b)
