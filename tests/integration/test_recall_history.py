"""recall_history tool (Phase D) against a scratch mongo db.

The reactive agent can pull a person's proactive timeline — episodes, beliefs,
and open commitments — through one typed, person-resolved window.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.integration


def _seed_person_with_history():
    from yoku.db.mongo import (
        beliefs_collection,
        ds_unified_users_collection,
        signals_collection,
    )
    from yoku.proactive.episodes import record_episode, set_understanding

    ds_unified_users_collection().insert_one(
        {
            "user_id": "u1",
            "jira": {"displayName": "Priya Sharma"},
            "github": {"login": "priya"},
            "slack": {"user_id": "U1"},
            "email": "p@x.io",
        }
    )
    record_episode(
        kind="sent",
        text="Hey Priya — AS-1 is Done with no PR.",
        person_user_id="u1",
        signal_id="s1",
        item_key="jira/AS-1",
        detector="done_no_pr",
        source="slack",
        outcome="sent",
    )
    eid = record_episode(
        kind="reply",
        text="we don't open PRs for spikes",
        person_user_id="u1",
        signal_id="s1",
        item_key="jira/AS-1",
        detector="done_no_pr",
        source="slack",
    )
    set_understanding(eid, {"outcome": "explains_as_normal", "learned_pattern": "spikes: no PRs"})
    beliefs_collection().insert_one(
        {
            "user_id": "u1",
            "pattern": "done_no_pr",
            "kind": "normal_workflow",
            "claim": "spikes: no PRs",
            "confidence": 0.7,
            "evidence_count": 2,
            "computed_at": datetime.now(UTC),
        }
    )
    signals_collection().insert_one(
        {
            "signal_id": uuid.uuid4().hex,
            "detector": "merged_no_ticket",
            "item_key": "github/o/r#9",
            "person_user_id": "u1",
            "status": "sent",
            "commitment": {
                "text": "link the ticket",
                "due": "2026-06-20",
                "made_at": datetime.now(UTC),
            },
        }
    )


@pytest.mark.integration
def test_recall_history_returns_timeline_beliefs_and_commitments(tenant):
    from yoku.agent.tools.recall_history import recall_history

    _seed_person_with_history()
    out = recall_history.invoke({"person": "Priya Sharma"})

    assert out["person"]["user_id"] == "u1"
    assert out["person"]["name"] == "Priya Sharma"

    kinds = {e["kind"] for e in out["episodes"]}
    assert {"sent", "reply"} <= kinds
    reply_ep = next(e for e in out["episodes"] if e["kind"] == "reply")
    assert reply_ep["outcome"] == "explains_as_normal"  # surfaced from understanding

    assert out["beliefs"][0]["claim"] == "spikes: no PRs"
    assert out["commitments"][0]["item_key"] == "github/o/r#9"
    assert out["commitments"][0]["text"] == "link the ticket"


@pytest.mark.integration
def test_recall_history_respects_limit_and_is_newest_first(tenant):
    from yoku.agent.tools.recall_history import recall_history
    from yoku.db.mongo import ds_unified_users_collection
    from yoku.proactive.episodes import record_episode

    ds_unified_users_collection().insert_one(
        {"user_id": "u2", "jira": {"displayName": "Sam Lee"}, "email": "s@x.io"}
    )
    for i in range(5):
        record_episode(
            kind="sent",
            text=f"nudge {i}",
            person_user_id="u2",
            signal_id=f"s{i}",
            item_key=f"jira/AS-{i}",
            detector="done_no_pr",
            source="engine",
            outcome="shadow",
            now=datetime(2026, 6, 1 + i, tzinfo=UTC),
        )
    out = recall_history.invoke({"person": "Sam Lee", "limit": 2})
    assert len(out["episodes"]) == 2
    assert out["episodes"][0]["text"] == "nudge 4"  # newest first


@pytest.mark.integration
def test_recall_history_errors_on_unknown_person(tenant):
    from yoku.agent.tools.recall_history import recall_history

    out = recall_history.invoke({"person": "Nobody At All"})
    assert "error" in out
