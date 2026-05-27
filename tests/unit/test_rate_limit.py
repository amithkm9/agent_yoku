"""RateLimit middleware: per-client fixed-window cap."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_yoku.middleware import RateLimit


def _app(limit: int, exempt=()):
    app = FastAPI()
    app.add_middleware(RateLimit, limit=limit, window_s=60, exempt_paths=exempt)

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
    client = _app(limit=2)
    # Different X-Forwarded-For = different client key.
    for _ in range(2):
        assert client.get("/api/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/api/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    # A different IP still has its full budget.
    assert client.get("/api/ping", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
