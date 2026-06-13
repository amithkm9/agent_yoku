"""Reply understanding (Phase B) against a scratch mongo db.

A person's Slack reply, once parsed, must change yoku's behaviour: a reply that
explains a gap suppresses it and teaches person memory; a promise is captured as
a commitment while the gap stays open; the LLM tier is budgeted and idempotent.
The `llm` seam is injected so these never reach OpenAI.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.integration


def _seed_reply(
    *,
    reply_text: str,
    detector: str = "done_no_pr",
    item_key: str = "jira/AS-1",
    person: str | None = "u1",
    status: str = "sent",
):
    """A live signal + its conversation + one un-understood reply episode."""
    from yoku.db.mongo import conversations_collection, signals_collection
    from yoku.proactive.episodes import record_episode

    sid = uuid.uuid4().hex
    signals_collection().insert_one(
        {
            "signal_id": sid,
            "detector": detector,
            "item_key": item_key,
            "person_user_id": person,
            "status": status,
            "matured_at": datetime.now(UTC),
            "verdict": {"real": True},
            "resolution": None,
        }
    )
    conversations_collection().insert_one(
        {
            "conversation_id": uuid.uuid4().hex,
            "signal_id": sid,
            "detector": detector,
            "item_key": item_key,
            "person_user_id": person,
            "message": "Hey — AS-1 is marked Done but I can't find a PR. Did it ship?",
            "state": "awaiting_reply",
            "replies": [],
        }
    )
    eid = record_episode(
        kind="reply",
        text=reply_text,
        person_user_id=person,
        signal_id=sid,
        item_key=item_key,
        detector=detector,
        source="slack",
    )
    return sid, eid


def _llm(verdict: dict):
    return lambda _prompt: verdict


@pytest.mark.integration
def test_explains_as_normal_suppresses_and_teaches_memory(tenant):
    from yoku.db.mongo import signals_collection
    from yoku.proactive.episodes import get_episodes
    from yoku.proactive.memory import get_memory, is_pattern_suppressed
    from yoku.proactive.reply_understanding import understand_replies

    sid, eid = _seed_reply(reply_text="we don't open PRs for spikes, it's just research")
    counts = understand_replies(
        llm=_llm(
            {
                "outcome": "explains_as_normal",
                "learned_pattern": "spikes/research tasks don't get PRs",
                "commitment": None,
                "reason": "team workflow",
            }
        ),
        budget=10,
    )
    assert counts["understood"] == 1

    # This signal is settled in the sticky state so the detector won't reopen it.
    sig = signals_collection().find_one({"signal_id": sid}, {"_id": 0})
    assert sig["status"] == "dismissed"
    assert sig["resolution"] == "explained"
    assert sig["verdict"]["suppressed_by"] == "reply"

    # Person memory learned: a dismissal counter (feeds the judge) + a note.
    mem = get_memory("u1")
    assert any(d["pattern"] == "done_no_pr" and d["by"] == "reply" for d in mem["dismissals"])
    assert any("spikes" in (p["note"] or "") for p in mem["patterns"])

    # The episode is enriched in place.
    ep = get_episodes(signal_id=sid, kinds=["reply"])[0]
    assert ep["understanding"]["outcome"] == "explains_as_normal"

    # A second such explanation would now suppress the pattern outright.
    from yoku.proactive.memory import record_dismissal

    record_dismissal("u1", "done_no_pr", "jira/AS-2", by="reply")
    assert is_pattern_suppressed("u1", "done_no_pr")


@pytest.mark.integration
def test_disputes_gap_settles_signal_without_a_general_dismissal(tenant):
    from yoku.db.mongo import signals_collection
    from yoku.proactive.memory import is_pattern_suppressed
    from yoku.proactive.reply_understanding import understand_replies

    sid, _ = _seed_reply(reply_text="that one shipped under AS-9, wrong flag")
    understand_replies(
        llm=_llm(
            {
                "outcome": "disputes_gap",
                "learned_pattern": None,
                "commitment": None,
                "reason": "shipped elsewhere",
            }
        ),
        budget=10,
    )
    sig = signals_collection().find_one({"signal_id": sid}, {"_id": 0})
    assert sig["status"] == "dismissed"
    assert sig["resolution"] == "disputed"
    # Item-specific dispute must NOT count toward suppressing the whole pattern.
    assert is_pattern_suppressed("u1", "done_no_pr") is False


@pytest.mark.integration
def test_promise_records_a_commitment_and_keeps_the_gap_open(tenant):
    from yoku.db.mongo import signals_collection
    from yoku.proactive.reply_understanding import understand_replies

    sid, _ = _seed_reply(reply_text="good catch, I'll link the PR tomorrow")
    understand_replies(
        llm=_llm(
            {
                "outcome": "promises",
                "learned_pattern": None,
                "commitment": {"text": "link the PR", "due": "2026-06-14"},
                "reason": "promised a fix",
            }
        ),
        budget=10,
    )
    sig = signals_collection().find_one({"signal_id": sid}, {"_id": 0})
    # The gap is real and unfixed — it stays live, but the promise is captured.
    assert sig["status"] == "sent"
    assert sig["commitment"]["text"] == "link the PR"
    assert sig["commitment"]["due"] == "2026-06-14"
    assert sig["commitment"]["source"] == "reply"
    assert sig["commitment"]["made_at"] is not None


@pytest.mark.integration
def test_acknowledged_only_enriches_the_episode(tenant):
    from yoku.db.mongo import signals_collection
    from yoku.proactive.episodes import get_episodes
    from yoku.proactive.reply_understanding import understand_replies

    sid, _ = _seed_reply(reply_text="ok thanks")
    understand_replies(
        llm=_llm(
            {
                "outcome": "acknowledged",
                "learned_pattern": None,
                "commitment": None,
                "reason": "ack",
            }
        ),
        budget=10,
    )
    sig = signals_collection().find_one({"signal_id": sid}, {"_id": 0})
    assert sig["status"] == "sent"  # untouched
    assert "commitment" not in sig
    ep = get_episodes(signal_id=sid, kinds=["reply"])[0]
    assert ep["understanding"]["outcome"] == "acknowledged"


@pytest.mark.integration
def test_budget_and_idempotency(tenant):
    from yoku.proactive.reply_understanding import understand_replies

    for i in range(3):
        _seed_reply(reply_text=f"reply {i}", item_key=f"jira/AS-{i}")

    llm = _llm(
        {"outcome": "acknowledged", "learned_pattern": None, "commitment": None, "reason": "ack"}
    )
    first = understand_replies(llm=llm, budget=2)
    assert first["candidates"] == 2 and first["understood"] == 2

    # Already-understood episodes drop out of the queue — the leftover drains.
    second = understand_replies(llm=llm, budget=10)
    assert second["candidates"] == 1 and second["understood"] == 1

    third = understand_replies(llm=llm, budget=10)
    assert third["candidates"] == 0


@pytest.mark.integration
def test_repromise_preserves_followup_count(tenant):
    """A second promise must not reset the follow-up counter (which would defeat
    MAX_FOLLOWUPS and let yoku nag repeatedly)."""
    from yoku.db.mongo import signals_collection
    from yoku.proactive.reply_understanding import understand_replies

    sid, _ = _seed_reply(reply_text="ok Monday now")
    signals_collection().update_one(
        {"signal_id": sid},
        {"$set": {"commitment": {"text": "link it", "due": "2026-06-10", "followups": 1}}},
    )
    understand_replies(
        llm=_llm(
            {
                "outcome": "promises",
                "learned_pattern": None,
                "commitment": {"text": "link it Monday", "due": "2026-06-16"},
                "reason": "re-promised",
            }
        ),
        budget=10,
    )
    sig = signals_collection().find_one({"signal_id": sid}, {"_id": 0})
    assert sig["commitment"]["text"] == "link it Monday"  # updated
    assert sig["commitment"]["followups"] == 1  # preserved, not reset to 0


@pytest.mark.integration
def test_explanation_teaches_memory_even_if_signal_already_healed(tenant):
    """A general 'this is normal' lesson is person-level — it must reach memory
    even when the specific gap self-healed before the reply was understood."""
    from yoku.db.mongo import signals_collection
    from yoku.proactive.memory import get_memory
    from yoku.proactive.reply_understanding import understand_replies

    sid, _ = _seed_reply(reply_text="we never open PRs for spikes", status="resolved")
    understand_replies(
        llm=_llm(
            {
                "outcome": "explains_as_normal",
                "learned_pattern": "spikes don't get PRs",
                "commitment": None,
                "reason": "workflow",
            }
        ),
        budget=10,
    )
    # Person memory learned the rule despite the signal already being settled.
    mem = get_memory("u1")
    assert any(d["pattern"] == "done_no_pr" and d["by"] == "reply" for d in mem["dismissals"])
    # The healed signal itself is left untouched.
    sig = signals_collection().find_one({"signal_id": sid}, {"_id": 0})
    assert sig["status"] == "resolved"


@pytest.mark.integration
def test_unknown_outcome_is_coerced_to_other(tenant):
    from yoku.proactive.episodes import get_episodes
    from yoku.proactive.reply_understanding import understand_replies

    sid, _ = _seed_reply(reply_text="🤔")
    understand_replies(llm=_llm({"outcome": "banana", "reason": "weird"}), budget=10)
    ep = get_episodes(signal_id=sid, kinds=["reply"])[0]
    assert ep["understanding"]["outcome"] == "other"


@pytest.mark.integration
def test_disabled_flag_is_a_noop(tenant, monkeypatch):
    from yoku.config import settings
    from yoku.proactive.reply_understanding import understand_replies

    monkeypatch.setattr(settings, "reply_understanding_enabled", False)
    _seed_reply(reply_text="we don't ticket chores")
    counts = understand_replies(llm=_llm({"outcome": "explains_as_normal"}), budget=10)
    assert counts.get("disabled") is True
    assert counts["understood"] == 0
