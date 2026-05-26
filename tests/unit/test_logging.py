"""Unit tests for agent_yoku.log + RequestContext middleware."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from agent_yoku.log import (
    SERVICE_NAME,
    _AgentYokuFormatter,
    _ContextFilter,
    _JsonFormatter,
    get_logger,
    safe_log_str,
    set_session,
)
from agent_yoku.middleware import (
    RequestContext,
    correlation_id_context,
    request_id_context,
    tenant_id_context,
)


def _record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="agent_yoku.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_safe_log_str_escapes_newlines() -> None:
    assert safe_log_str("a\nb\rc") == "a\\nb\\rc"
    assert safe_log_str(None) == ""
    assert safe_log_str(42) == "42"


def test_context_filter_stamps_defaults() -> None:
    request_id_context.set("")
    correlation_id_context.set("")
    tenant_id_context.set("")
    set_session(None)

    rec = _record()
    assert _ContextFilter().filter(rec) is True
    assert rec.service_name == SERVICE_NAME
    assert rec.request_id == "unknown-request-id"
    assert rec.correlation_id == "unknown-correlation-id"
    assert rec.tenant_id == "unknown-tenant-id"
    assert rec.session_id == "-"


def test_context_filter_picks_up_bound_values() -> None:
    request_id_context.set("req-123")
    correlation_id_context.set("corr-456")
    tenant_id_context.set("acme")
    set_session("sess-abcdef0123")

    rec = _record()
    _ContextFilter().filter(rec)
    assert rec.request_id == "req-123"
    assert rec.correlation_id == "corr-456"
    assert rec.tenant_id == "acme"
    assert rec.session_id == "sess-abc"  # truncated to 8


def test_formatter_backfills_missing_attrs() -> None:
    rec = _record()
    # Deliberately do not run the filter — simulates a log record from a
    # third-party logger that hasn't been stamped.
    out = _AgentYokuFormatter("%(service_name)s|%(request_id)s|%(message)s").format(rec)
    assert out == "unknown-service|unknown-request-id|hello"


def test_json_formatter_emits_required_fields() -> None:
    rec = _record("payload")
    _ContextFilter().filter(rec)
    line = _JsonFormatter().format(rec)
    payload = json.loads(line)
    assert payload["msg"] == "payload"
    assert payload["level"] == "INFO"
    assert payload["service_name"] == SERVICE_NAME
    assert "request_id" in payload
    assert "correlation_id" in payload
    assert "tenant_id" in payload
    assert "session_id" in payload


def test_get_logger_returns_namespaced_child() -> None:
    log = get_logger("unit.test")
    assert log.name == "agent_yoku.unit.test"
    # propagation off so messages don't leak to root in test runs
    assert logging.getLogger("agent_yoku").propagate is False


# --------- middleware ---------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestContext)

    @app.get("/echo")
    async def echo() -> dict:
        return {
            "request_id": request_id_context.get(),
            "correlation_id": correlation_id_context.get(),
            "tenant_id": tenant_id_context.get(),
        }

    return TestClient(app)


def test_middleware_generates_ids_when_headers_absent(client: TestClient) -> None:
    resp = client.get("/echo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"]
    assert body["correlation_id"]
    assert body["tenant_id"] == "unknown-tenant"
    # response should echo the IDs back
    assert resp.headers["x-request-id"] == body["request_id"]
    assert resp.headers["x-correlation-id"] == body["correlation_id"]
    assert resp.headers["x-tenant-id"] == "unknown-tenant"


def test_middleware_propagates_inbound_ids(client: TestClient) -> None:
    resp = client.get(
        "/echo",
        headers={
            "X-Request-ID": "req-from-caller",
            "X-Correlation-ID": "corr-from-caller",
            "X-Tenant-ID": "acme",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "req-from-caller"
    assert body["correlation_id"] == "corr-from-caller"
    assert body["tenant_id"] == "acme"
    assert resp.headers["x-request-id"] == "req-from-caller"
    assert resp.headers["x-correlation-id"] == "corr-from-caller"
    assert resp.headers["x-tenant-id"] == "acme"
