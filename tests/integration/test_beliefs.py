"""Belief consolidation (Phase C) against a scratch mongo db.

Episodes distil into durable per-person beliefs with recency-weighted confidence,
confirmations subtract, decayed evidence drops below the floor — and the judge's
person dimension is handed those beliefs as evidence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)


def _explain(person, detector, note, *, days_ago=0, item_key="jira/AS-1"):
    from yoku.proactive.episodes import record_episode, set_understanding

    eid = record_episode(
        kind="reply",
        text=note,
        person_user_id=person,
        detector=detector,
        item_key=item_key,
        signal_id=uuid.uuid4().hex,
        source="slack",
        now=NOW - timedelta(days=days_ago),
    )
    set_understanding(eid, {"outcome": "explains_as_normal", "learned_pattern": note})


def _verdict_episode(kind, person, detector, *, days_ago=0):
    from yoku.proactive.episodes import record_episode

    record_episode(
        kind=kind,
        text=f"{kind} of {detector}",
        person_user_id=person,
        detector=detector,
        item_key="jira/AS-x",
        signal_id=uuid.uuid4().hex,
        source="inbox",
        now=NOW - timedelta(days=days_ago),
    )


@pytest.mark.integration
def test_explanation_forms_a_belief(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _explain("u1", "done_no_pr", "spikes and research tasks don't get PRs")
    counts = consolidate_beliefs(now=NOW)
    assert counts == {"beliefs": 1, "people": 1}

    beliefs = get_beliefs("u1")
    assert len(beliefs) == 1
    b = beliefs[0]
    assert b["pattern"] == "done_no_pr"
    assert b["claim"] == "spikes and research tasks don't get PRs"
    assert b["kind"] == "normal_workflow"
    assert b["confidence"] == 0.5  # one recent explanation: 1/(1+1)
    assert b["evidence_count"] == 1


@pytest.mark.integration
def test_repeated_dismissals_form_a_generic_belief(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _verdict_episode("dismissal", "u1", "merged_no_ticket")
    _verdict_episode("dismissal", "u1", "merged_no_ticket")
    consolidate_beliefs(now=NOW)

    b = get_beliefs("u1")[0]
    assert b["pattern"] == "merged_no_ticket"
    assert "dismissed as not a gap" in b["claim"]
    assert b["confidence"] >= 0.4
    assert b["evidence_count"] == 2


@pytest.mark.integration
def test_a_single_dismissal_is_below_the_floor(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _verdict_episode("dismissal", "u1", "done_no_pr")
    consolidate_beliefs(now=NOW)
    # 0.6 / 1.6 = 0.38 < MIN_CONFIDENCE — one weak signal must not become a fact.
    assert get_beliefs("u1") == []


@pytest.mark.integration
def test_a_confirmation_cancels_an_explanation(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _explain("u1", "done_no_pr", "design work has no code")
    _verdict_episode("confirm", "u1", "done_no_pr")  # it WAS a real gap once
    consolidate_beliefs(now=NOW)
    # support 1.0 - against 1.0 = 0 → no belief.
    assert get_beliefs("u1") == []


@pytest.mark.integration
def test_decay_drops_stale_evidence(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _explain("recent", "done_no_pr", "spikes don't get PRs", days_ago=0)
    _explain("stale", "done_no_pr", "spikes don't get PRs", days_ago=120)  # r=0.25
    consolidate_beliefs(now=NOW)

    assert len(get_beliefs("recent")) == 1  # 0.50 — kept
    assert get_beliefs("stale") == []  # 0.20 — decayed below the floor


@pytest.mark.integration
def test_recent_evidence_outweighs_older(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _explain("fresh", "done_no_pr", "n", days_ago=0)
    _explain("fresh", "done_no_pr", "n", days_ago=0, item_key="jira/AS-2")
    _explain("older", "done_no_pr", "n", days_ago=60)
    _explain("older", "done_no_pr", "n", days_ago=60, item_key="jira/AS-2")
    consolidate_beliefs(now=NOW)

    assert get_beliefs("fresh")[0]["confidence"] > get_beliefs("older")[0]["confidence"]


@pytest.mark.integration
def test_get_beliefs_filters_by_pattern_and_confidence(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _explain("u1", "done_no_pr", "spikes")
    _explain("u1", "merged_no_ticket", "chore PRs", item_key="github/o/r#1")
    consolidate_beliefs(now=NOW)

    assert {b["pattern"] for b in get_beliefs("u1")} == {"done_no_pr", "merged_no_ticket"}
    only = get_beliefs("u1", pattern="done_no_pr")
    assert len(only) == 1 and only[0]["pattern"] == "done_no_pr"
    assert get_beliefs("u1", min_confidence=0.99) == []


@pytest.mark.integration
def test_consolidation_is_idempotent(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs, get_beliefs

    _explain("u1", "done_no_pr", "spikes")
    first = consolidate_beliefs(now=NOW)
    second = consolidate_beliefs(now=NOW)
    assert first == second
    assert len(get_beliefs("u1")) == 1  # no duplicate rows


@pytest.mark.integration
def test_judge_person_evidence_includes_beliefs(tenant):
    from yoku.proactive.beliefs import consolidate_beliefs
    from yoku.proactive.judge import _person_evidence

    _explain("u1", "done_no_pr", "spikes don't get PRs")
    consolidate_beliefs(now=NOW)

    ev = _person_evidence(
        {"person_user_id": "u1", "detector": "done_no_pr", "person_name": "Priya"}
    )
    assert ev["learned_beliefs"] != "none"
    assert ev["learned_beliefs"][0]["claim"] == "spikes don't get PRs"
    assert ev["learned_beliefs"][0]["confidence"] == 0.5


@pytest.mark.integration
def test_judge_passes_beliefs_into_the_person_prompt(tenant):
    """The wired-in belief must actually reach the person-dimension LLM call."""
    from yoku.db.mongo import signals_collection
    from yoku.proactive.beliefs import consolidate_beliefs
    from yoku.proactive.judge import judge_signals

    _explain("u1", "done_no_pr", "spikes never get PRs")
    consolidate_beliefs(now=NOW)

    signals_collection().insert_one(
        {
            "signal_id": "sig1",
            "detector": "done_no_pr",
            "kind": "drift",
            "item_key": "jira/AS-9",
            "person_user_id": "u1",
            "person_name": "Priya",
            "title": "Investigate latency spike",
            "evidence": {},
            "status": "open",
            "matured_at": NOW,
            "verdict": None,
            "label": None,
        }
    )

    captured: list[tuple[str, str]] = []

    def fake_llm(kind: str, prompt: str) -> dict:
        captured.append((kind, prompt))
        return {"real": True} if kind == "item" else {"normal_for_person": True, "reason": "belief"}

    judge_signals(now=NOW, llm=fake_llm, budget=10)

    person_prompts = [p for (k, p) in captured if k == "person"]
    assert person_prompts and "spikes never get PRs" in person_prompts[0]
