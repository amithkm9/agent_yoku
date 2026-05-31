"""Tests for the JIRA issue -> mongo doc flattener, focused on epic/parent links."""

from __future__ import annotations

import pytest

from yoku.pipeline.connectors._runtime import JiraConfig, use_jira
from yoku.pipeline.connectors.jira.client import issue_to_doc


@pytest.fixture(autouse=True)
def _bound_config():
    cfg = JiraConfig(
        base_url="https://asato.atlassian.net",
        email="bot@asato.dev",
        token="x",
        project="AS",
    )
    with use_jira(cfg):
        yield


def _issue(key: str, issuetype: str, parent: dict | None = None) -> dict:
    fields: dict = {"summary": f"summary for {key}", "issuetype": {"name": issuetype}}
    if parent is not None:
        fields["parent"] = parent
    return {"key": key, "fields": fields}


def _parent(key: str, issuetype: str) -> dict:
    return {"key": key, "fields": {"issuetype": {"name": issuetype}}}


def test_story_under_epic_sets_epic_key():
    doc = issue_to_doc(_issue("AS-2", "Story", parent=_parent("AS-1", "Epic")))
    assert doc["epic_key"] == "AS-1"
    assert doc["parent_key"] == "AS-1"


def test_subtask_under_story_sets_parent_only():
    # A Sub-task's parent is its Story, not the Epic — epic_key stays None and is
    # resolved at query time via the Story's epic_key.
    doc = issue_to_doc(_issue("AS-3", "Sub-task", parent=_parent("AS-2", "Story")))
    assert doc["parent_key"] == "AS-2"
    assert doc["epic_key"] is None


def test_orphan_task_has_no_links():
    doc = issue_to_doc(_issue("AS-9", "Task"))
    assert doc["epic_key"] is None
    assert doc["parent_key"] is None


def test_epic_itself_has_no_links():
    doc = issue_to_doc(_issue("AS-1", "Epic"))
    assert doc["epic_key"] is None
    assert doc["parent_key"] is None


def test_parent_missing_issuetype_records_parent_only():
    doc = issue_to_doc(_issue("AS-4", "Task", parent={"key": "AS-1"}))
    assert doc["parent_key"] == "AS-1"
    assert doc["epic_key"] is None
