"""Smoke tests for Pydantic DTOs."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_yoku.models import (
    ChatMessage,
    ChatSession,
    GitHubPR,
    JiraTicket,
    PRStatus,
    UnifiedUser,
)
from agent_yoku.models.user import JiraUserBlock


def test_jira_ticket_minimal():
    t = JiraTicket(key="AS-1")
    assert t.key == "AS-1"
    assert t.labels == []
    assert t.fix_versions == []


def test_github_pr_status_enum():
    pr = GitHubPR(key="AsatoCorp/x#1", repo="AsatoCorp/x", number=1, status="merged")
    assert pr.status == PRStatus.MERGED


def test_unified_user_jira_only():
    u = UnifiedUser(
        user_id="abc",
        email="a@b.com",
        jira=JiraUserBlock(displayName="A B"),
        match_source="jira_only",
    )
    assert u.github is None
    assert u.jira.displayName == "A B"


def test_chat_session_required_fields():
    now = datetime.now(UTC)
    s = ChatSession(session_id="sid", created_at=now, last_active_at=now)
    assert s.turn_count == 0


def test_chat_message_with_tool_call():
    now = datetime.now(UTC)
    m = ChatMessage(
        session_id="s",
        turn_id="t",
        turn_seq=1,
        msg_idx=0,
        role="ai",
        content="",
        tool_calls=[{"id": "c", "name": "get", "args": {}}],
        created_at=now,
    )
    assert m.tool_calls[0]["name"] == "get"
