"""Slack bot voice (M6): outbound messenger guardrails + inbound events endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_SECRET = "test-signing-secret"
_TEAM = "T0TESTTEAM"


# ---------- outbound: messenger ----------


def _seed_unified(slack_user_id="U123", is_bot=False):
    from yoku.db.mongo import ds_unified_users_collection

    ds_unified_users_collection().insert_one(
        {
            "user_id": "u_p",
            "email": "priya@x.io",
            "jira": {"displayName": "Priya Sharma"},
            "slack": {"user_id": slack_user_id, "display_name": "Priya", "is_bot": is_bot},
            "is_bot": is_bot,
        }
    )


@pytest.mark.integration
def test_send_dm_dry_run_resolves_target(tenant):
    from yoku.proactive.messenger import send_dm

    _seed_unified()
    out = send_dm("priya@x.io", "hello", dry_run=True)
    assert out["dry_run"] is True
    assert out["target"]["slack_user_id"] == "U123"
    assert out["would_send"] == "hello"


@pytest.mark.integration
def test_send_dm_refuses_without_slack_identity_or_bot(tenant):
    from yoku.db.mongo import ds_unified_users_collection
    from yoku.proactive.messenger import send_dm

    ds_unified_users_collection().insert_one(
        {"user_id": "u_x", "email": "nox@x.io", "jira": {"displayName": "No Slack"}}
    )
    assert "no Slack identity" in send_dm("nox@x.io", "hi")["error"]

    _seed_unified(slack_user_id="UBOT", is_bot=True)
    assert "bot" in send_dm("priya@x.io", "hi")["error"]
    assert "error" in send_dm("nobody@x.io", "hi")


@pytest.mark.integration
def test_send_dm_real_posts_via_client(tenant, monkeypatch):
    from yoku.proactive import messenger

    _seed_unified()
    calls: list = []
    monkeypatch.setattr(
        "yoku.connectors.slack.client.open_dm", lambda uid: calls.append(("open", uid)) or "D77"
    )
    monkeypatch.setattr(
        "yoku.connectors.slack.client.post_message",
        lambda ch, text, thread_ts=None: calls.append(("post", ch, text))
        or {"channel": ch, "ts": "1.2"},
    )
    out = messenger.send_dm("priya@x.io", "ping", dry_run=False)
    assert out["sent"] == "ping" and out["channel"] == "D77"
    assert calls == [("open", "U123"), ("post", "D77", "ping")]


# ---------- inbound: events endpoint ----------


def _signed_headers(body: bytes, secret: str = _SECRET, ts: str | None = None) -> dict:
    ts = ts or str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def _event_payload(text="will close it tomorrow", event_id=None) -> dict:
    return {
        "type": "event_callback",
        "team_id": _TEAM,
        "event_id": event_id or f"Ev{uuid.uuid4().hex[:10]}",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D77",
            "user": "U123",
            "text": text,
            "ts": "1700000001.000100",
        },
    }


@pytest.fixture
def voice_tenant(tenant, monkeypatch):
    """Tenant with a slack config carrying team_id + signing secret."""
    from yoku.db import connector_configs as cc
    from yoku.routers import slack_events

    cc.upsert_config(
        name="slack",
        config={"workspace": "test", "team_id": _TEAM},
        secrets={"bot_token": "xoxb-test", "signing_secret": _SECRET},
        user_id="u_admin",
    )
    # Route the team map straight at this tenant — avoids scanning all dbs.
    monkeypatch.setattr(
        slack_events, "_find_tenant_for_team", lambda t: (tenant, _SECRET) if t == _TEAM else None
    )
    return tenant


@pytest.mark.integration
def test_url_verification_challenge(tenant):
    from yoku.main import app

    r = TestClient(app).post(
        "/api/slack/events", json={"type": "url_verification", "challenge": "c123"}
    )
    assert r.status_code == 200 and r.json() == {"challenge": "c123"}


@pytest.mark.integration
def test_inbound_dm_requires_valid_signature(voice_tenant):
    from yoku.db import tenancy
    from yoku.db.mongo import slack_inbound_collection
    from yoku.main import app

    client = TestClient(app)
    body = json.dumps(_event_payload()).encode()

    # Bad signature → rejected.
    r = client.post(
        "/api/slack/events",
        content=body,
        headers={**_signed_headers(body, secret="wrong"), "Content-Type": "application/json"},
    )
    assert r.status_code == 401

    # Stale timestamp → rejected (replay window).
    r = client.post(
        "/api/slack/events",
        content=body,
        headers={
            **_signed_headers(body, ts=str(int(time.time()) - 9000)),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401

    # Good signature → stored.
    r = client.post(
        "/api/slack/events",
        content=body,
        headers={**_signed_headers(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    tenancy.set_tenant(voice_tenant)
    row = slack_inbound_collection().find_one({}, {"_id": 0})
    assert row["event_type"] == "dm" and row["slack_user_id"] == "U123"
    assert row["text"] == "will close it tomorrow" and row["processed"] is False


@pytest.mark.integration
def test_inbound_dedupes_retries_and_ignores_bot_echo(voice_tenant):
    from yoku.db import tenancy
    from yoku.db.mongo import slack_inbound_collection
    from yoku.main import app

    client = TestClient(app)
    payload = _event_payload(event_id="Ev_dup")
    body = json.dumps(payload).encode()
    for _ in range(2):  # Slack retries deliveries
        r = client.post(
            "/api/slack/events",
            content=body,
            headers={**_signed_headers(body), "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    echo = _event_payload(event_id="Ev_echo")
    echo["event"]["bot_id"] = "B99"  # the bot's own message
    body2 = json.dumps(echo).encode()
    client.post(
        "/api/slack/events",
        content=body2,
        headers={**_signed_headers(body2), "Content-Type": "application/json"},
    )

    tenancy.set_tenant(voice_tenant)
    assert slack_inbound_collection().count_documents({}) == 1  # one event, no echo


@pytest.mark.integration
def test_unknown_team_is_rejected(tenant, monkeypatch):
    from yoku.main import app
    from yoku.routers import slack_events

    monkeypatch.setattr(slack_events, "_find_tenant_for_team", lambda t: None)
    body = json.dumps(_event_payload()).encode()
    r = TestClient(app).post(
        "/api/slack/events",
        content=body,
        headers={**_signed_headers(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 404
