"""Event delta against a scratch mongo db: write path + linked events."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def tenant(scratch_db):
    from yoku.db import tenancy

    tid = f"ev_{uuid.uuid4().hex[:8]}"
    tenancy.set_tenant(tid)
    yield tid
    from pymongo import MongoClient

    from yoku.config import settings
    from yoku.db.tenancy import tenant_db_name

    MongoClient(settings.mongo_uri).drop_database(tenant_db_name(tid))
    tenancy.set_tenant(None)


@pytest.mark.integration
def test_write_events_persists_rows(tenant):
    from yoku.db.mongo import events_collection
    from yoku.proactive.events import diff_events, write_events

    n = write_events(diff_events("dc-jira", "AS-1", None, {"status": "To Do"}))
    assert n == 1
    rows = list(events_collection().find({}, {"_id": 0}))
    assert rows[0]["kind"] == "created" and rows[0]["doc_key"] == "AS-1"
    assert write_events([]) == 0


@pytest.mark.integration
def test_pr_link_pass_emits_linked_event_once(tenant, monkeypatch):
    from yoku.db.mongo import dc_github_collection, dc_jira_collection, events_collection
    from yoku.pipeline import pr_to_jira

    monkeypatch.setattr("sys.argv", ["link"])
    dc_jira_collection().insert_one({"key": "AS-1", "summary": "t"})
    dc_github_collection().insert_one(
        {
            "key": "Org/repo#7",
            "jira_keys": ["AS-1", "AS-MISSING"],
            "status": "merged",
            "merged": True,
        }
    )

    pr_to_jira.main()
    linked = list(events_collection().find({"kind": "linked"}, {"_id": 0}))
    # One event for the ticket that exists; none for the dangling AS-MISSING ref.
    assert [(e["doc_key"], e["new"]) for e in linked] == [("AS-1", "Org/repo#7")]

    # Second pass: the link already exists — no duplicate event.
    pr_to_jira.main()
    assert events_collection().count_documents({"kind": "linked"}) == 1

    # Dangling ref never starts firing either.
    assert events_collection().count_documents({"doc_key": "AS-MISSING"}) == 0


@pytest.mark.integration
def test_full_sync_cycle_created_then_updated(tenant):
    """Simulates two syncs of the same doc through the ingest seam contract."""
    from yoku.db.mongo import dc_jira_collection, events_collection
    from yoku.proactive.events import TRACKED_FIELDS, diff_events, write_events

    coll = dc_jira_collection()
    proj = {"text": 1, "embedding": 1, **dict.fromkeys(TRACKED_FIELDS["dc-jira"], 1)}

    # Sync 1: doc is new.
    doc1 = {
        "key": "AS-9",
        "status": "In Progress",
        "assignee": "Priya",
        "summary": "s",
        "text": "t",
    }
    existing = coll.find_one({"key": "AS-9"}, proj)
    write_events(diff_events("dc-jira", "AS-9", existing, doc1))
    coll.update_one({"key": "AS-9"}, {"$set": doc1}, upsert=True)

    # Sync 2: status flips.
    doc2 = dict(doc1, status="Done")
    existing = coll.find_one({"key": "AS-9"}, proj)
    write_events(diff_events("dc-jira", "AS-9", existing, doc2))
    coll.update_one({"key": "AS-9"}, {"$set": doc2}, upsert=True)

    # Sync 3: nothing changed.
    existing = coll.find_one({"key": "AS-9"}, proj)
    write_events(diff_events("dc-jira", "AS-9", existing, doc2))

    kinds = [(e["kind"], e.get("field")) for e in events_collection().find({}).sort("ts", 1)]
    assert kinds == [("created", None), ("updated", "status")]
    updated = events_collection().find_one({"kind": "updated"})
    assert (updated["old"], updated["new"]) == ("In Progress", "Done")
