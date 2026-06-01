"""describe_collection surfaces the rich schema the agent needs to build queries:
filter semantics, enum values, and cross-collection join keys."""

from __future__ import annotations

import pytest

from yoku.agent import tools


class _FakeColl:
    def aggregate(self, _pipeline):
        return iter([])  # no sample docs -> empty example lists


@pytest.fixture
def _fake(monkeypatch):
    monkeypatch.setattr(tools, "get_collection", lambda _name: _FakeColl())


@pytest.mark.unit
def test_reports_source_key_and_relationships(_fake):
    out = tools.describe_collection.invoke({"collection": "dc-github"})
    assert out["source"] == "github"
    assert out["key_example"]
    assert any(r["with"] == "dc-jira" for r in out["relationships"])


@pytest.mark.unit
def test_person_field_exposes_kind_and_identity(_fake):
    fields = tools.describe_collection.invoke({"collection": "dc-github"})["fields"]
    assert fields["author"]["filter_kind"] == "user"
    assert fields["author"]["identity"] == "github.login"


@pytest.mark.unit
def test_closed_set_field_exposes_enum(_fake):
    fields = tools.describe_collection.invoke({"collection": "dc-github"})["fields"]
    assert "merged" in fields["status"]["enum"]


@pytest.mark.unit
def test_join_field_reports_links_to(_fake):
    fields = tools.describe_collection.invoke({"collection": "dc-github"})["fields"]
    assert fields["jira_keys"]["links_to"] == "dc-jira.key"


@pytest.mark.unit
def test_non_filterable_field_has_no_filter_metadata(_fake):
    fields = tools.describe_collection.invoke({"collection": "dc-github"})["fields"]
    assert "filter_arg" not in fields["description"]


@pytest.mark.unit
def test_unknown_collection_returns_error(monkeypatch):
    def boom(_name):
        raise ValueError("Unknown collection 'nope'")

    monkeypatch.setattr(tools, "get_collection", boom)
    out = tools.describe_collection.invoke({"collection": "nope"})
    assert "error" in out
