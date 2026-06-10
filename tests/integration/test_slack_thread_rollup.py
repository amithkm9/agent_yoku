"""Integration tests for the Slack thread rollup against a scratch mongo db."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def tenant(scratch_db):
    from yoku.db import tenancy

    tid = f"thr_{uuid.uuid4().hex[:8]}"
    tenancy.set_tenant(tid)
    yield tid
    from pymongo import MongoClient

    from yoku.config import settings
    from yoku.db.tenancy import tenant_db_name

    MongoClient(settings.mongo_uri).drop_database(tenant_db_name(tid))
    tenancy.set_tenant(None)


def _msg(channel_id, ts, text, *, thread_ts=None, jira_keys=None, embedding=None):
    is_reply = bool(thread_ts and thread_ts != ts)
    return {
        "key": f"{channel_id}/{ts}",
        "channel_id": channel_id,
        "channel_name": "eng",
        "ts": ts,
        "thread_ts": thread_ts,
        "is_thread_reply": is_reply,
        "text": text,
        "jira_keys": jira_keys or [],
        "reply_count": 0,
        "embedding": embedding,
    }


@pytest.mark.integration
def test_rollup_builds_whole_thread_on_parent(tenant):
    from yoku.db.mongo import dc_slack_collection
    from yoku.pipeline.slack_threads import rollup_threads

    coll = dc_slack_collection()
    coll.insert_many(
        [
            _msg("C1", "100.0", "[#eng] priya: shipped the rate limiter", embedding=[0.1]),
            _msg(
                "C1",
                "101.0",
                "[#eng] marco: which ticket?",
                thread_ts="100.0",
                jira_keys=["AS-4396"],
            ),
            _msg("C1", "102.0", "[#eng] priya: AS-4396, closing tomorrow", thread_ts="100.0"),
        ]
    )

    stats = rollup_threads()
    assert stats == {"threads": 1, "updated": 1, "orphans": 0}

    parent = coll.find_one({"key": "C1/100.0"})
    assert parent["text"] == (
        "[#eng] priya: shipped the rate limiter"
        "\n↳ marco: which ticket?"
        "\n↳ priya: AS-4396, closing tomorrow"
    )
    assert parent["_base_text"] == "[#eng] priya: shipped the rate limiter"
    assert parent["jira_keys"] == ["AS-4396"]
    assert parent["reply_count"] == 2
    assert parent["embedding"] is None  # re-embed with the full discussion

    # Replies stay addressable, untouched.
    reply = coll.find_one({"key": "C1/101.0"})
    assert reply["text"] == "[#eng] marco: which ticket?"


@pytest.mark.integration
def test_rollup_is_idempotent_and_preserves_embedding(tenant):
    from yoku.db.mongo import dc_slack_collection
    from yoku.pipeline.slack_threads import rollup_threads

    coll = dc_slack_collection()
    coll.insert_many(
        [
            _msg("C1", "100.0", "[#eng] a: parent"),
            _msg("C1", "101.0", "[#eng] b: reply", thread_ts="100.0"),
        ]
    )
    rollup_threads()
    coll.update_one({"key": "C1/100.0"}, {"$set": {"embedding": [0.5]}})  # embed step ran

    stats = rollup_threads()
    assert stats["updated"] == 0
    parent = coll.find_one({"key": "C1/100.0"})
    assert parent["embedding"] == [0.5]  # unchanged thread keeps its embedding

    # A new reply re-rolls and re-clears the embedding.
    coll.insert_one(_msg("C1", "102.0", "[#eng] c: late reply", thread_ts="100.0"))
    stats = rollup_threads()
    assert stats["updated"] == 1
    parent = coll.find_one({"key": "C1/100.0"})
    assert parent["text"].endswith("↳ c: late reply")
    assert parent["embedding"] is None


@pytest.mark.integration
def test_orphan_replies_are_skipped(tenant):
    from yoku.db.mongo import dc_slack_collection
    from yoku.pipeline.slack_threads import rollup_threads

    dc_slack_collection().insert_one(
        _msg("C1", "201.0", "[#eng] x: reply to missing parent", thread_ts="200.0")
    )
    stats = rollup_threads()
    assert stats == {"threads": 1, "updated": 0, "orphans": 1}
