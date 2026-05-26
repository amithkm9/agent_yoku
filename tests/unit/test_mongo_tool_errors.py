"""Error-path coverage for the generic mongo tools."""

from __future__ import annotations

from agent_yoku.agent import tools


def test_mongo_count_unknown_collection_returns_error(fake_collections):
    out = tools.mongo_count.invoke({"collection": "does_not_exist"})
    assert "error" in out
    assert "Unknown collection" in out["error"]
    assert "does_not_exist" in out["error"]


def test_mongo_query_unknown_collection_returns_error(fake_collections):
    out = tools.mongo_query.invoke({"collection": "ghost", "pipeline": [{"$match": {}}]})
    assert "error" in out


def test_mongo_query_blocked_stage_returns_error(fake_collections):
    out = tools.mongo_query.invoke(
        {
            "collection": "jira_tickets",
            "pipeline": [{"$out": "evil"}],
        }
    )
    assert "error" in out
    assert "blocked" in out["error"].lower()


def test_mongo_query_multi_key_stage_returns_error(fake_collections):
    out = tools.mongo_query.invoke(
        {
            "collection": "jira_tickets",
            "pipeline": [{"$match": {}, "extra": {}}],
        }
    )
    assert "error" in out


def test_mongo_query_appends_default_limit(fake_collections):
    tools.mongo_query.invoke(
        {
            "collection": "jira_tickets",
            "pipeline": [{"$match": {"status": "open"}}],
        }
    )
    last_pipeline = fake_collections["jira_tickets"].aggregate_calls[-1]
    assert {"$limit": 100} in last_pipeline


def test_mongo_query_caps_explicit_limit_at_100(fake_collections):
    tools.mongo_query.invoke(
        {
            "collection": "jira_tickets",
            "pipeline": [{"$match": {}}, {"$limit": 9999}],
        }
    )
    last_pipeline = fake_collections["jira_tickets"].aggregate_calls[-1]
    # whichever stage now has $limit should be 100, not 9999
    limits = [s["$limit"] for s in last_pipeline if "$limit" in s]
    assert limits and max(limits) == 100
