"""Commitment follow-ups (Phase D) against a scratch mongo db.

A promise captured from a reply, left unfulfilled past its deadline while the
gap is still live, earns exactly one gentle chase — shadowed by default, bounded
by MAX_FOLLOWUPS, and recorded as a `followup` episode.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)


def _seed(*, commitment: dict, status: str = "sent", person: str = "u1"):
    from yoku.db.mongo import (
        conversations_collection,
        ds_unified_users_collection,
        signals_collection,
    )

    sid = uuid.uuid4().hex
    ds_unified_users_collection().insert_one(
        {
            "user_id": person,
            "jira": {"displayName": "Priya Sharma"},
            "slack": {"user_id": "U1", "display_name": "priya"},
            "email": "p@x.io",
        }
    )
    signals_collection().insert_one(
        {
            "signal_id": sid,
            "detector": "done_no_pr",
            "item_key": "jira/AS-1",
            "person_user_id": person,
            "person_name": "Priya Sharma",
            "status": status,
            "commitment": commitment,
        }
    )
    conversations_collection().insert_one(
        {
            "conversation_id": uuid.uuid4().hex,
            "signal_id": sid,
            "detector": "done_no_pr",
            "item_key": "jira/AS-1",
            "person_user_id": person,
            "slack_user_id": "U1",
            "message": "Hey — AS-1 is Done with no PR.",
            "state": "awaiting_reply",
            "replies": [],
        }
    )
    return sid


@pytest.mark.integration
def test_lapsed_promise_is_chased_once_in_shadow(tenant):
    from yoku.db.mongo import conversations_collection, signals_collection
    from yoku.proactive.episodes import KIND_FOLLOWUP, get_episodes
    from yoku.proactive.orchestrator import run_commitment_followups

    sid = _seed(commitment={"text": "link the PR", "due": "2026-06-10", "made_at": NOW})

    counts = run_commitment_followups(now=NOW)
    assert counts["due"] == 1
    assert counts["shadowed"] == 1
    assert counts["sent"] == 0  # no send keys configured → shadow

    sig = signals_collection().find_one({"signal_id": sid}, {"_id": 0})
    assert sig["commitment"]["followups"] == 1
    assert sig["commitment"]["last_followup_at"] is not None

    ep = get_episodes(signal_id=sid, kinds=[KIND_FOLLOWUP])
    assert len(ep) == 1
    assert ep[0]["outcome"] == "shadow"
    assert ep[0]["source"] == "engine"
    assert "AS-1" in ep[0]["text"]

    convo = conversations_collection().find_one({"signal_id": sid}, {"_id": 0})
    assert len(convo["followups"]) == 1


@pytest.mark.integration
def test_promise_not_yet_due_is_left_alone(tenant):
    from yoku.proactive.orchestrator import run_commitment_followups

    _seed(commitment={"text": "link the PR", "due": "2026-06-20", "made_at": NOW})
    counts = run_commitment_followups(now=NOW)
    assert counts["due"] == 0


@pytest.mark.integration
def test_no_due_date_uses_grace_window(tenant):
    from yoku.proactive.orchestrator import run_commitment_followups

    # No explicit due date, but made 3 days ago → past the 2-day grace.
    _seed(commitment={"text": "link the PR", "due": None, "made_at": NOW - timedelta(days=3)})
    counts = run_commitment_followups(now=NOW)
    assert counts["due"] == 1


@pytest.mark.integration
def test_already_followed_up_is_not_chased_again(tenant):
    from yoku.proactive.orchestrator import run_commitment_followups

    _seed(
        commitment={"text": "link the PR", "due": "2026-06-10", "made_at": NOW, "followups": 1},
    )
    counts = run_commitment_followups(now=NOW)
    assert counts["due"] == 0  # at MAX_FOLLOWUPS, skipped before counting


@pytest.mark.integration
def test_healed_gap_is_not_chased(tenant):
    """A resolved signal (gap fixed) carries no live status, so it's ignored."""
    from yoku.proactive.orchestrator import run_commitment_followups

    _seed(
        commitment={"text": "link the PR", "due": "2026-06-10", "made_at": NOW},
        status="resolved",
    )
    counts = run_commitment_followups(now=NOW)
    assert counts["due"] == 0


@pytest.mark.integration
def test_disabled_flag_is_a_noop(tenant, monkeypatch):
    from yoku.config import settings
    from yoku.proactive.orchestrator import run_commitment_followups

    monkeypatch.setattr(settings, "commitment_followups_enabled", False)
    _seed(commitment={"text": "link the PR", "due": "2026-06-10", "made_at": NOW})
    counts = run_commitment_followups(now=NOW)
    assert counts.get("disabled") is True
