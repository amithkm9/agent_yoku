"""list_collections surfaces per-source details so the prompt needn't hardcode them."""

from __future__ import annotations

import pytest

from yoku.agent import tools


class _FakeColl:
    def estimated_document_count(self):
        return 0

    def index_information(self):
        return {"_id_": {"key": [("_id", 1)]}, "key_1": {"key": [("key", 1)]}}


@pytest.fixture
def _fake_allowed(monkeypatch):
    factories = {
        "jira_tickets": _FakeColl,
        "slack_messages": _FakeColl,
        "users": _FakeColl,
    }
    monkeypatch.setattr(tools, "ALLOWED_COLLECTIONS", factories)


@pytest.mark.unit
def test_source_collections_report_full_details(_fake_allowed):
    by_name = {c["name"]: c for c in tools.list_collections.invoke({})}

    jira = by_name["jira_tickets"]
    assert jira["source"] == "jira"
    assert jira["key_example"] == "AS-4163"
    assert jira["description"]  # non-empty
    assert "assignee" in jira["filter_fields"]
    assert "key" in jira["indexed_fields"]


@pytest.mark.unit
def test_slack_collection_now_has_description(_fake_allowed):
    slack = next(c for c in tools.list_collections.invoke({}) if c["name"] == "slack_messages")
    assert slack["source"] == "slack"
    assert slack["description"]  # previously empty — the gap this change closes
    assert slack["key_example"]
    assert "channel" in slack["filter_fields"]


@pytest.mark.unit
def test_directory_collection_has_no_source(_fake_allowed):
    users = next(c for c in tools.list_collections.invoke({}) if c["name"] == "users")
    assert "source" not in users
    assert "filter_fields" not in users
    assert users["description"]  # from the directory description map
