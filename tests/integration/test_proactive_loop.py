"""Speak-first orchestrator (M7) against a scratch mongo db.

Slack is never hit: send paths monkeypatch the client; everything else is
shadow mode (the default). Pins: shadow drafting, conversation dedupe,
never-guess routing, the three-key send gate, daily cap, reply threading.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def _seed_person(user_id="u_p", slack_id="U123"):
    from yoku.db.mongo import ds_unified_users_collection

    ds_unified_users_collection().insert_one(
        {
            "user_id": user_id,
            "email": f"{user_id}@x.io",
            "jira": {"displayName": "Priya Sharma"},
            "slack": {"user_id": slack_id, "display_name": "Priya"} if slack_id else None,
        }
    )


def _seed_signal(item_key="jira/AS-1", user_id="u_p", detector="done_no_pr", real=True):
    from yoku.db.mongo import signals_collection

    doc = {
        "signal_id": uuid.uuid4().hex,
        "detector": detector,
        "kind": "drift",
        "item_key": item_key,
        "title": "Add rate limiting to the chat API",
        "person_user_id": user_id,
        "person_name": "Priya Sharma",
        "evidence": {"status": "Done", "gap_age_days": 9},
        "confidence": 0.7,
        "url": None,
        "status": "open",
        "label": None,
        "resolution": None,
        "verdict": {"real": real, "judged_at": _NOW} if real is not None else None,
        "first_seen_at": _NOW,
        "matured_at": _NOW,
        "last_seen_at": _NOW,
        "created_at": _NOW,
    }
    signals_collection().insert_one(doc)
    return doc


@pytest.mark.integration
def test_shadow_mode_drafts_one_message_per_gap(tenant):
    from yoku.db.mongo import conversations_collection, signals_collection
    from yoku.proactive.orchestrator import run_proactive_loop

    _seed_person()
    _seed_signal()
    counts = run_proactive_loop(now=_NOW)
    assert counts["shadowed"] == 1 and counts["sent"] == 0

    s = signals_collection().find_one({})
    assert s["status"] == "shadow"
    assert "AS-1" in s["proposed_message"]
    assert s["proposed_message"].endswith("?")  # one clear ask
    assert "Priya" in s["proposed_message"]

    convo = conversations_collection().find_one({})
    assert convo["state"] == "shadow" and convo["signal_id"] == s["signal_id"]

    # Re-run: dedupe — never two threads on one gap.
    counts = run_proactive_loop(now=_NOW)
    assert counts["shadowed"] == 0
    assert conversations_collection().count_documents({}) == 1


@pytest.mark.integration
def test_unjudged_or_rejected_signals_never_speak(tenant):
    from yoku.db.mongo import conversations_collection
    from yoku.proactive.orchestrator import run_proactive_loop

    _seed_person()
    _seed_signal(item_key="jira/AS-2", real=None)  # not yet judged
    _seed_signal(item_key="jira/AS-3", real=False)  # judge said not real
    counts = run_proactive_loop(now=_NOW)
    assert counts["eligible"] == 0
    assert conversations_collection().count_documents({}) == 0


@pytest.mark.integration
def test_no_slack_identity_means_skip_not_guess(tenant):
    from yoku.db.mongo import signals_collection
    from yoku.proactive.orchestrator import run_proactive_loop

    _seed_person(slack_id=None)
    _seed_signal()
    counts = run_proactive_loop(now=_NOW)
    assert counts["skipped_no_target"] == 1
    # Stays open in the Inbox for humans — no draft fabricated.
    s = signals_collection().find_one({})
    assert s["status"] == "open" and "proposed_message" not in s


@pytest.mark.integration
def test_send_path_requires_all_three_keys_and_posts(tenant, monkeypatch):
    from yoku.db import connector_configs as cc
    from yoku.db.mongo import conversations_collection, signals_collection
    from yoku.proactive.orchestrator import run_proactive_loop

    _seed_person()
    _seed_signal()
    cc.upsert_config(
        name="slack",
        config={"workspace": "t", "proactive_send_enabled": True},
        secrets={"bot_token": "xoxb-test"},
        user_id="u_admin",
    )
    posted: list = []
    monkeypatch.setattr("yoku.connectors.slack.client.open_dm", lambda uid: "D9")
    monkeypatch.setattr(
        "yoku.connectors.slack.client.post_message",
        lambda ch, text, thread_ts=None: posted.append((ch, text)) or {"channel": ch, "ts": "9.9"},
    )

    counts = run_proactive_loop(now=_NOW)
    assert counts["sent"] == 1 and counts["shadowed"] == 0
    assert posted and posted[0][0] == "D9"
    assert signals_collection().find_one({})["status"] == "sent"
    convo = conversations_collection().find_one({})
    assert convo["state"] == "awaiting_reply" and convo["slack_channel"] == "D9"


@pytest.mark.integration
def test_global_kill_switch_forces_shadow(tenant, monkeypatch):
    from yoku.config import settings
    from yoku.db import connector_configs as cc
    from yoku.db.mongo import signals_collection
    from yoku.proactive.orchestrator import run_proactive_loop

    _seed_person()
    _seed_signal()
    cc.upsert_config(
        name="slack",
        config={"workspace": "t", "proactive_send_enabled": True},
        secrets={"bot_token": "xoxb-test"},
        user_id="u_admin",
    )
    monkeypatch.setattr(settings, "proactive_speak_enabled", False)
    counts = run_proactive_loop(now=_NOW)
    assert counts["sent"] == 0 and counts["shadowed"] == 1
    assert signals_collection().find_one({})["status"] == "shadow"


@pytest.mark.integration
def test_daily_cap_defers_second_dm_to_same_person(tenant, monkeypatch):
    from yoku.db import connector_configs as cc
    from yoku.db.mongo import conversations_collection
    from yoku.proactive.orchestrator import run_proactive_loop

    _seed_person()
    _seed_signal(item_key="jira/AS-1")
    _seed_signal(item_key="jira/AS-2")
    cc.upsert_config(
        name="slack",
        config={"workspace": "t", "proactive_send_enabled": True},
        secrets={"bot_token": "xoxb-test"},
        user_id="u_admin",
    )
    monkeypatch.setattr("yoku.connectors.slack.client.open_dm", lambda uid: "D9")
    monkeypatch.setattr(
        "yoku.connectors.slack.client.post_message",
        lambda ch, text, thread_ts=None: {"channel": ch, "ts": "9.9"},
    )

    counts = run_proactive_loop(now=_NOW)
    assert counts["sent"] == 1 and counts["deferred"] == 1  # cap = 1/person/day
    assert conversations_collection().count_documents({}) == 1


@pytest.mark.integration
def test_reply_is_threaded_onto_awaiting_conversation(tenant):
    from yoku.db.mongo import conversations_collection, slack_inbound_collection
    from yoku.proactive.orchestrator import process_inbound_replies

    conversations_collection().insert_one(
        {
            "conversation_id": "c1",
            "signal_id": "s1",
            "item_key": "jira/AS-1",
            "person_user_id": "u_p",
            "slack_user_id": "U123",
            "message": "hey",
            "state": "awaiting_reply",
            "replies": [],
            "opened_at": _NOW - timedelta(hours=2),
            "last_message_at": _NOW - timedelta(hours=2),
        }
    )
    slack_inbound_collection().insert_many(
        [
            {
                "event_id": "Ev1",
                "event_type": "dm",
                "slack_user_id": "U123",
                "text": "ah yes — closing it today",
                "ts": "1.1",
                "received_at": _NOW,
                "processed": False,
            },
            {  # DM from someone with no open conversation: marked processed, not threaded
                "event_id": "Ev2",
                "event_type": "dm",
                "slack_user_id": "U999",
                "text": "hi bot",
                "ts": "1.2",
                "received_at": _NOW,
                "processed": False,
            },
        ]
    )

    assert process_inbound_replies(now=_NOW) == 1
    convo = conversations_collection().find_one({})
    assert convo["state"] == "replied"
    assert convo["replies"][0]["text"] == "ah yes — closing it today"
    assert slack_inbound_collection().count_documents({"processed": False}) == 0
