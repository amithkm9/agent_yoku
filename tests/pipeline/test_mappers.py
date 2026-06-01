"""Unit tests for the source -> typed-primitive mappers (pure functions, no I/O)."""

from __future__ import annotations

from yoku.mappers import (
    map_github_pr,
    map_jira_ticket,
    map_slack_message,
)
from yoku.schemas import Conversation, Primitive, PullRequest, WorkItem


def test_github_pr_maps_to_typed_pull_request():
    raw = {
        "key": "AsatoCorp/agent-svc#173",
        "repo": "AsatoCorp/agent-svc",
        "number": 173,
        "summary": "Add memory",
        "description": "body text",
        "status": "merged",
        "author": "alice",
        "url": "https://gh/173",
        "updated": "2026-05-01",
        "merged": True,
        "is_draft": False,
        "base": "main",
        "head": "feature",
        "jira_keys": ["AS-1", "AS-2"],
        "text": "blob",
    }
    u = map_github_pr(raw)
    assert isinstance(u, PullRequest)
    assert u.domain == Primitive.PULL_REQUEST
    assert u.provider == "github"
    assert u.key == "github/AsatoCorp/agent-svc#173"
    assert u.title == "Add memory"
    assert u.refs == ["jira/AS-1", "jira/AS-2"]
    # structured fields are first-class and typed (no meta junk drawer)
    assert u.repo == "AsatoCorp/agent-svc"
    assert u.number == 173
    assert u.merged is True
    assert u.base == "main"


def test_jira_ticket_maps_to_typed_work_item():
    raw = {
        "key": "AS-4163",
        "summary": "Ship feature",
        "description": "desc",
        "status": "Done",
        "assignee": "bob@asato.ai",
        "url": "https://jira/AS-4163",
        "updated": "2026-05-02",
        # real stored shape: linked_prs are LinkedPR dicts, issue type is `issuetype`
        "linked_prs": [{"key": "AsatoCorp/agent-svc#173"}],
        "issuetype": "Story",
        "priority": "High",
        "epic_key": "AS-1000",
        "labels": ["mem"],
    }
    u = map_jira_ticket(raw)
    assert isinstance(u, WorkItem)
    assert u.domain == Primitive.WORK_ITEM
    assert u.key == "jira/AS-4163"
    assert u.author == "bob@asato.ai"
    assert u.refs == ["github/AsatoCorp/agent-svc#173"]
    assert u.issue_type == "Story"
    assert u.priority == "High"
    assert u.epic_key == "AS-1000"
    assert u.labels == ["mem"]


def test_slack_message_maps_to_typed_conversation():
    raw = {
        "key": "C0AB123/1700000000.123456",
        # real stored shape: channel_name / author_name / url
        "channel_name": "eng",
        "channel_id": "C0AB123",
        "author_name": "carol",
        "text": "we agreed to deprecate v1",
        "ts": "1700000000.123456",
        "url": "https://slack/x",
        "jira_keys": ["AS-9"],
        "reply_count": 12,
        "thread_ts": "1700000000.000001",
    }
    u = map_slack_message(raw)
    assert isinstance(u, Conversation)
    assert u.domain == Primitive.CONVERSATION
    assert u.key == "slack/C0AB123/1700000000.123456"
    assert u.body == "we agreed to deprecate v1"
    assert u.refs == ["jira/AS-9"]
    assert u.channel == "eng"
    assert u.channel_type == "channel"
    assert u.reply_count == 12
    assert u.url == "https://slack/x"


def test_missing_link_fields_yield_empty_refs():
    assert map_github_pr({"key": "o/r#1"}).refs == []
    assert map_jira_ticket({"key": "AS-1"}).refs == []
    assert map_slack_message({"key": "C/1.2"}).refs == []


def test_jira_linked_prs_accepts_bare_strings_too():
    """Be lenient: a linked_prs entry may be a bare key string, not a dict."""
    u = map_jira_ticket({"key": "AS-2", "linked_prs": ["o/r#5", {"key": "o/r#6"}]})
    assert u.refs == ["github/o/r#5", "github/o/r#6"]
