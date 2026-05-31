"""Tests for the GitHub PR -> mongo doc flattener."""

from __future__ import annotations

from yoku.pipeline.connectors.github.client import is_bot, pr_to_doc


def _fake_pr(**overrides):
    base = {
        "number": 42,
        "title": "Fix Safari login (closes AS-1234)",
        "body": "## Summary\n…",
        "state": "closed",
        "draft": False,
        "merged_at": "2026-04-22T10:14:00Z",
        "user": {"login": "internet-zero"},
        "assignees": [{"login": "internet-zero"}],
        "labels": [{"name": "bug"}, {"name": "frontend"}],
        "head": {"ref": "reddy/AS-1234-fix"},
        "base": {"ref": "main"},
        "comments": 7,
        "created_at": "2026-04-20T00:00:00Z",
        "updated_at": "2026-04-22T10:14:00Z",
        "html_url": "https://github.com/AsatoCorp/agent-svc/pull/42",
    }
    base.update(overrides)
    return base


def test_synthesizes_key():
    doc = pr_to_doc(_fake_pr(), "AsatoCorp/agent-svc")
    assert doc["key"] == "AsatoCorp/agent-svc#42"


def test_merged_status_inferred_from_merged_at():
    doc = pr_to_doc(_fake_pr(), "AsatoCorp/agent-svc")
    assert doc["status"] == "merged"
    assert doc["merged"] is True


def test_draft_status_takes_precedence_when_open():
    pr = _fake_pr(merged_at=None, state="open", draft=True)
    doc = pr_to_doc(pr, "AsatoCorp/agent-svc")
    assert doc["status"] == "draft"


def test_open_status_when_not_merged_or_draft():
    pr = _fake_pr(merged_at=None, state="open", draft=False)
    doc = pr_to_doc(pr, "AsatoCorp/agent-svc")
    assert doc["status"] == "open"


def test_extracts_jira_keys():
    doc = pr_to_doc(_fake_pr(), "AsatoCorp/agent-svc")
    assert "AS-1234" in doc["jira_keys"]


def test_labels_flattened_to_strings():
    doc = pr_to_doc(_fake_pr(), "AsatoCorp/agent-svc")
    assert doc["labels"] == ["bug", "frontend"]


def test_is_bot_detects_brackets():
    assert is_bot({"user": {"login": "dependabot[bot]"}}) is True
    assert is_bot({"user": {"login": "internet-zero"}}) is False
