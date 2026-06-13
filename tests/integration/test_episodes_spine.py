"""Episodic memory spine (Phase A) against a scratch mongo db.

Every real interaction must leave one episode: a human verdict in the Inbox
(confirm/dismissal), yoku speaking (sent — real or shadow), and a person's
reply threaded back. Nothing reads episodes for decisions yet; this suite only
asserts they are captured with the right shape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _seed_matured_signal(tenant, *, with_person=True):
    """A Done-no-PR ticket detected into one matured, owned signal."""
    from yoku.db.mongo import dc_jira_collection, ds_unified_users_collection, signals_collection
    from yoku.proactive.signals import run_detectors

    dc_jira_collection().insert_one(
        {
            "key": "AS-1",
            "summary": "Add rate limiting",
            "status": "Done",
            "assignee": "Priya Sharma",
            "linked_prs": [],
            "updated": _iso_days_ago(7),
        }
    )
    if with_person:
        ds_unified_users_collection().insert_one(
            {
                "user_id": "u1",
                "jira": {"displayName": "Priya Sharma"},
                "slack": {"user_id": "U1", "display_name": "priya"},
                "email": "p@x.io",
            }
        )
    run_detectors()
    return signals_collection().find_one({"detector": "done_no_pr"}, {"_id": 0})


@pytest.mark.integration
def test_dismiss_records_a_dismissal_episode(tenant):
    from yoku.proactive.episodes import KIND_DISMISSAL, get_episodes
    from yoku.proactive.signals import label_signal

    s = _seed_matured_signal(tenant)
    label_signal(s["signal_id"], "dismissed", "u_admin")

    eps = get_episodes(signal_id=s["signal_id"])
    assert len(eps) == 1
    ep = eps[0]
    assert ep["kind"] == KIND_DISMISSAL
    assert ep["person_user_id"] == "u1"
    assert ep["detector"] == "done_no_pr"
    assert ep["item_key"] == "jira/AS-1"
    assert ep["source"] == "inbox"
    assert ep["outcome"] == "dismissed"
    assert "Dismissed" in ep["text"]


@pytest.mark.integration
def test_confirm_records_a_confirm_episode(tenant):
    from yoku.proactive.episodes import KIND_CONFIRM, get_episodes
    from yoku.proactive.signals import label_signal

    s = _seed_matured_signal(tenant)
    label_signal(s["signal_id"], "confirmed", "u_admin")

    eps = get_episodes(person_user_id="u1", kinds=[KIND_CONFIRM])
    assert len(eps) == 1
    assert eps[0]["outcome"] == "confirmed"
    assert eps[0]["signal_id"] == s["signal_id"]


@pytest.mark.integration
def test_shadow_speak_records_a_sent_episode(tenant):
    """With no send keys (default), the loop shadows — still a `sent` episode,
    flagged outcome=shadow over the internal channel."""
    from yoku.db.mongo import signals_collection
    from yoku.proactive.episodes import KIND_SENT, get_episodes
    from yoku.proactive.orchestrator import run_proactive_loop

    s = _seed_matured_signal(tenant)
    # The speak loop only acts on judged-real signals; in the full pipeline the
    # judge sets this verdict. Set it directly to isolate the orchestrator.
    signals_collection().update_one(
        {"signal_id": s["signal_id"]},
        {"$set": {"verdict": {"real": True}}},
    )

    counts = run_proactive_loop()
    assert counts["shadowed"] == 1

    eps = get_episodes(signal_id=s["signal_id"], kinds=[KIND_SENT])
    assert len(eps) == 1
    assert eps[0]["kind"] == KIND_SENT
    assert eps[0]["outcome"] == "shadow"
    assert eps[0]["source"] == "engine"
    assert eps[0]["person_user_id"] == "u1"
    # The stored text is the actual nudge yoku composed.
    assert "AS-1" in eps[0]["text"]


@pytest.mark.integration
def test_inbound_reply_records_a_reply_episode(tenant):
    from yoku.db.mongo import signals_collection, slack_inbound_collection
    from yoku.proactive.episodes import KIND_REPLY, get_episodes
    from yoku.proactive.orchestrator import process_inbound_replies, run_proactive_loop

    s = _seed_matured_signal(tenant)
    signals_collection().update_one(
        {"signal_id": s["signal_id"]},
        {"$set": {"verdict": {"real": True}}},
    )
    run_proactive_loop()  # opens the (shadow) conversation for U1

    # A real reply would only thread onto an awaiting_reply conversation, so
    # simulate the conversation having been sent for real.
    from yoku.db.mongo import conversations_collection

    conversations_collection().update_one(
        {"signal_id": s["signal_id"]}, {"$set": {"state": "awaiting_reply"}}
    )
    slack_inbound_collection().insert_one(
        {
            "event_id": "ev1",
            "event_type": "dm",
            "slack_user_id": "U1",
            "text": "yeah, shipped under #212 — will link it",
            "ts": "1700000000.1",
            "received_at": datetime.now(UTC),
            "processed": False,
        }
    )

    threaded = process_inbound_replies()
    assert threaded == 1

    eps = get_episodes(signal_id=s["signal_id"], kinds=[KIND_REPLY])
    assert len(eps) == 1
    assert eps[0]["person_user_id"] == "u1"
    assert eps[0]["source"] == "slack"
    assert "link it" in eps[0]["text"]


@pytest.mark.integration
def test_get_episodes_scopes_by_person_and_orders_newest_first(tenant):
    from yoku.proactive.episodes import get_episodes, record_episode

    record_episode(
        kind="confirm",
        text="first",
        person_user_id="uA",
        source="inbox",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    record_episode(
        kind="dismissal",
        text="second",
        person_user_id="uA",
        source="inbox",
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )
    record_episode(
        kind="confirm",
        text="other person",
        person_user_id="uB",
        source="inbox",
        now=datetime(2026, 1, 3, tzinfo=UTC),
    )

    eps = get_episodes(person_user_id="uA")
    assert [e["text"] for e in eps] == ["second", "first"]  # newest first
    assert all(e["person_user_id"] == "uA" for e in eps)


@pytest.mark.integration
def test_record_episode_rejects_unknown_kind(tenant):
    from yoku.proactive.episodes import get_episodes, record_episode

    assert record_episode(kind="bogus", text="x", source="engine") is None
    assert get_episodes() == []
