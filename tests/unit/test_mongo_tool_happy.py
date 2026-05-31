"""Happy-path coverage for the mongo_* agent tools.

Tool `.invoke()` returns the raw Python object (dict/list), not a JSON string —
mirror the existing tool tests and assert on it directly.
"""

from __future__ import annotations

import pytest

from yoku.agent import tools


@pytest.mark.unit
def test_mongo_query_returns_rows(fake_collections):
    fake_collections["jira_tickets"].docs = [{"key": "A-1"}, {"key": "A-2"}]
    out = tools.mongo_query.invoke({"collection": "jira_tickets", "pipeline": [{"$match": {}}]})
    assert out["count"] == 2
    assert {r["key"] for r in out["results"]} == {"A-1", "A-2"}


@pytest.mark.unit
def test_mongo_query_strips_hidden_fields(fake_collections):
    fake_collections["jira_tickets"].docs = [
        {"key": "A-1", "embedding": [1.0], "text": "secret blob"}
    ]
    out = tools.mongo_query.invoke({"collection": "jira_tickets", "pipeline": [{"$match": {}}]})
    row = out["results"][0]
    assert "embedding" not in row
    assert "text" not in row


@pytest.mark.unit
def test_mongo_count_with_filter_is_not_estimated(fake_collections):
    fake_collections["jira_tickets"].docs = [{"key": "A-1"}, {"key": "A-2"}]
    out = tools.mongo_count.invoke({"collection": "jira_tickets", "filter": {"status": "open"}})
    assert out["count"] == 2
    assert out["estimated"] is False


@pytest.mark.unit
def test_mongo_count_no_filter_is_estimated(fake_collections):
    fake_collections["github_prs"].docs = [{"key": "o/r#1"}]
    out = tools.mongo_count.invoke({"collection": "github_prs"})
    assert out["count"] == 1
    assert out["estimated"] is True


@pytest.mark.unit
def test_describe_collection_returns_schema(fake_collections):
    out = tools.describe_collection.invoke({"collection": "jira_tickets"})
    assert "error" not in out
    assert out["collection"] == "jira_tickets"
    assert out["description"]
    assert isinstance(out["fields"], dict) and out["fields"]


@pytest.mark.unit
def test_describe_collection_unknown_returns_error(fake_collections):
    out = tools.describe_collection.invoke({"collection": "totally_unknown"})
    assert "error" in out
    assert "allowed" in out


@pytest.mark.unit
def test_describe_collection_sample_size_clamped(fake_collections):
    out = tools.describe_collection.invoke({"collection": "jira_tickets", "sample_size": 99999})
    assert "error" not in out


class _FakeColl:
    def estimated_document_count(self):
        return 0

    def index_information(self):
        return {"_id_": {"key": [("_id", 1)]}, "key_1": {"key": [("key", 1)]}}


@pytest.mark.unit
def test_list_collections_happy(monkeypatch):
    monkeypatch.setattr(
        tools,
        "ALLOWED_COLLECTIONS",
        {"jira_tickets": _FakeColl, "github_prs": _FakeColl},
    )
    out = tools.list_collections.invoke({})
    names = {c["name"] for c in out}
    assert names == {"jira_tickets", "github_prs"}
    jira = next(c for c in out if c["name"] == "jira_tickets")
    assert jira["count"] == 0
    assert jira["source"] == "jira"
