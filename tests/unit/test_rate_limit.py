"""RateLimit middleware: per-client fixed-window cap."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_yoku.middleware import RateLimit


def _app(limit: int, exempt=(), trust_forwarded_for=False):
    app = FastAPI()
    app.add_middleware(
        RateLimit,
        limit=limit,
        window_s=60,
        exempt_paths=exempt,
        trust_forwarded_for=trust_forwarded_for,
    )

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return TestClient(app)


@pytest.mark.unit
def test_blocks_after_limit():
    client = _app(limit=3)
    assert [client.get("/api/ping").status_code for _ in range(3)] == [200, 200, 200]
    resp = client.get("/api/ping")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


@pytest.mark.unit
def test_exempt_path_is_never_limited():
    client = _app(limit=2, exempt=("/healthz",))
    codes = [client.get("/healthz").status_code for _ in range(5)]
    assert codes == [200] * 5


@pytest.mark.unit
def test_separate_clients_have_separate_budgets():
    # X-Forwarded-For is only trusted when explicitly enabled (behind a proxy).
    client = _app(limit=2, trust_forwarded_for=True)
    for _ in range(2):
        assert client.get("/api/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/api/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    # A different IP still has its full budget.
    assert client.get("/api/ping", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


@pytest.mark.unit
def test_forwarded_for_ignored_by_default():
    # Without trust, spoofing X-Forwarded-For can't dodge the limit (shared socket IP).
    client = _app(limit=2)
    headers = [{"X-Forwarded-For": f"9.9.9.{i}"} for i in range(5)]
    codes = [client.get("/api/ping", headers=h).status_code for h in headers]
    assert 429 in codes, "spoofed IPs must not each get a fresh budget"
