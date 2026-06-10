"""Event-delta diff logic (proactive Phase 1) — pure, no mongo."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yoku.proactive.events import _VALUE_MAX_CHARS, diff_events, linked_event

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


def test_new_doc_emits_single_created_event():
    events = diff_events("dc-jira", "AS-1", None, {"status": "To Do"}, _NOW)
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "created"
    assert e["doc_key"] == "AS-1"
    assert e["canonical_key"] == "jira/AS-1"
    assert e["collection"] == "dc-jira"
    assert e["field"] is None
    assert e["processed"] is False
    assert e["ts"] == _NOW


def test_tracked_field_change_emits_updated_event_per_field():
    existing = {"status": "In Progress", "assignee": "Priya", "summary": "rate limit"}
    doc = {"status": "Done", "assignee": "Marco", "summary": "rate limit"}
    events = diff_events("dc-jira", "AS-1", existing, doc, _NOW)
    assert [(e["field"], e["old"], e["new"]) for e in events] == [
        ("status", "In Progress", "Done"),
        ("assignee", "Priya", "Marco"),
    ]
    assert all(e["kind"] == "updated" for e in events)


def test_unchanged_doc_emits_nothing():
    doc = {"status": "Done", "assignee": "Priya", "summary": "s"}
    assert diff_events("dc-jira", "AS-1", dict(doc), doc, _NOW) == []


def test_untracked_field_change_is_ignored():
    # `text` changes on every re-roll/edit but is not a tracked field.
    events = diff_events(
        "dc-github", "o/r#1", {"status": "open", "text": "a"}, {"status": "open", "text": "b"}, _NOW
    )
    assert events == []


def test_slack_is_created_only():
    assert diff_events("dc-slack", "C1/1.0", None, {}, _NOW)[0]["kind"] == "created"
    # any field change on an existing message: no events
    assert diff_events("dc-slack", "C1/1.0", {"text": "a"}, {"text": "b"}, _NOW) == []


def test_long_values_are_clipped():
    long = "x" * 1000
    events = diff_events("dc-jira", "AS-1", {"summary": "s"}, {"summary": long}, _NOW)
    assert len(events[0]["new"]) == _VALUE_MAX_CHARS


def test_linked_event_shape():
    e = linked_event("AS-1", "Org/repo#7", _NOW)
    assert e["kind"] == "linked"
    assert e["doc_key"] == "AS-1"
    assert e["canonical_key"] == "jira/AS-1"
    assert e["field"] == "linked_prs"
    assert e["new"] == "Org/repo#7"
