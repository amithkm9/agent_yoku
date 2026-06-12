"""Detector → signal lifecycle against a scratch mongo db.

Covers the M3 'done when' criteria: a Done-no-PR ticket surfaces once matured,
dismissal is sticky across re-runs, and a healed gap records self_healed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _done_ticket(key="AS-1", days_old=7, assignee="Priya Sharma", linked=None):
    return {
        "key": key,
        "summary": "Add rate limiting",
        "status": "Done",
        "assignee": assignee,
        "linked_prs": linked or [],
        "updated": _iso_days_ago(days_old),
    }


@pytest.mark.integration
def test_old_gap_matures_immediately_and_carries_person(tenant):
    from yoku.db.mongo import dc_jira_collection, ds_unified_users_collection, signals_collection
    from yoku.proactive.signals import run_detectors

    dc_jira_collection().insert_one(_done_ticket(days_old=7))
    ds_unified_users_collection().insert_one(
        {"user_id": "u1", "jira": {"displayName": "Priya Sharma"}, "email": "p@x.io"}
    )

    stats = run_detectors()
    assert stats["done_no_pr"]["new"] == 1

    s = signals_collection().find_one({"detector": "done_no_pr"}, {"_id": 0})
    assert s["item_key"] == "jira/AS-1"
    assert s["status"] == "open"
    assert s["matured_at"] is not None  # gap already older than the window
    assert s["person_user_id"] == "u1"
    assert s["person_name"] == "Priya Sharma"


@pytest.mark.integration
def test_fresh_gap_waits_for_maturation(tenant):
    from yoku.db.mongo import dc_jira_collection, signals_collection
    from yoku.proactive.signals import run_detectors

    dc_jira_collection().insert_one(_done_ticket(days_old=0))
    run_detectors()
    s = signals_collection().find_one({"detector": "done_no_pr"})
    assert s["status"] == "open"
    assert s["matured_at"] is None  # too fresh — not Inbox-visible yet

    # Same gap, re-detected after the window has passed: matures by first_seen age.
    future = datetime.now(UTC) + timedelta(days=4)
    run_detectors(now=future)
    s = signals_collection().find_one({"detector": "done_no_pr"})
    assert s["matured_at"] is not None


@pytest.mark.integration
def test_dismissal_is_sticky_across_reruns(tenant):
    from yoku.db.mongo import dc_jira_collection, signals_collection
    from yoku.proactive.signals import label_signal, run_detectors

    dc_jira_collection().insert_one(_done_ticket(days_old=7))
    run_detectors()
    s = signals_collection().find_one({"detector": "done_no_pr"})

    label_signal(s["signal_id"], "dismissed", "u_admin")
    run_detectors()  # gap still present in the data

    s = signals_collection().find_one({"detector": "done_no_pr"})
    assert s["status"] == "dismissed"
    assert s["resolution"] == "dismissed"
    assert s["labeled_by"] == "u_admin"


@pytest.mark.integration
def test_healed_gap_records_self_healed_and_can_reopen(tenant):
    from yoku.db.mongo import dc_jira_collection, signals_collection
    from yoku.proactive.signals import run_detectors

    coll = dc_jira_collection()
    coll.insert_one(_done_ticket(days_old=7))
    run_detectors()

    # A PR gets linked — the gap is gone next sync.
    coll.update_one({"key": "AS-1"}, {"$set": {"linked_prs": [{"key": "Org/r#1"}]}})
    stats = run_detectors()
    assert stats["done_no_pr"]["self_healed"] == 1
    s = signals_collection().find_one({"detector": "done_no_pr"})
    assert s["status"] == "resolved"
    assert s["resolution"] == "self_healed"

    # The link disappears again — fresh occurrence, fresh maturation clock.
    coll.update_one({"key": "AS-1"}, {"$set": {"linked_prs": []}})
    stats = run_detectors()
    assert stats["done_no_pr"]["reopened"] == 1
    s = signals_collection().find_one({"detector": "done_no_pr"})
    assert s["status"] == "open" and s["resolution"] is None


@pytest.mark.integration
def test_aging_out_of_window_is_not_self_healing(tenant):
    """An unfixed gap sliding past the recency window records `aged_out` —
    the outcome stream must not count it as a fix."""
    from yoku.db.mongo import dc_jira_collection, signals_collection
    from yoku.proactive.signals import run_detectors

    coll = dc_jira_collection()
    coll.insert_one(_done_ticket(days_old=10))
    run_detectors()

    # Days pass without anyone touching the ticket: its `updated` (mirrored
    # into the signal's evidence at detection) is now older than the moving
    # 14-day window floor — out of scope, but the gap was never fixed.
    from yoku.db.mongo import signals_collection as _signals

    coll.update_one({"key": "AS-1"}, {"$set": {"updated": _iso_days_ago(20)}})
    _signals().update_one({}, {"$set": {"evidence.updated": _iso_days_ago(20)}})
    stats = run_detectors()
    assert stats["done_no_pr"]["aged_out"] == 1
    assert stats["done_no_pr"]["self_healed"] == 0
    s = signals_collection().find_one({"detector": "done_no_pr"})
    assert s["resolution"] == "aged_out"


@pytest.mark.integration
def test_merged_no_ticket_detector(tenant):
    from yoku.db.mongo import dc_github_collection, signals_collection
    from yoku.proactive.signals import run_detectors

    dc_github_collection().insert_one(
        {
            "key": "Org/repo#9",
            "summary": "hotfix: tighten retry loop",
            "status": "merged",
            "author": "kcsraju",
            "repo": "Org/repo",
            "jira_keys": [],
            "updated": _iso_days_ago(5),
        }
    )
    stats = run_detectors()
    assert stats["merged_no_ticket"]["new"] == 1
    s = signals_collection().find_one({"detector": "merged_no_ticket"}, {"_id": 0})
    assert s["item_key"] == "github/Org/repo#9"
    assert s["matured_at"] is not None
