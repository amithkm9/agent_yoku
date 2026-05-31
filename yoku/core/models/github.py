"""Pydantic schema for GitHub entities stored in mongo (source of truth)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from yoku.core.models._fields import doc_field


class PRStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    DRAFT = "draft"


class GitHubPR(BaseModel):
    """AsatoCorp GitHub PRs — code that does the work."""

    model_config = ConfigDict(extra="allow")

    key: str = doc_field(
        "PR key 'org/repo#number', e.g. 'AsatoCorp/agent-svc#173'.", default=..., display=True
    )
    repo: str = doc_field(
        "Full repo name, e.g. 'AsatoCorp/agent-svc'.",
        default=...,
        display=True,
        filter_arg="repo",
        normalize="gh_repo",
    )
    number: int = doc_field("PR number within the repo.", default=...)
    summary: str | None = doc_field("PR title.", display=True)
    description: str | None = doc_field("PR body text.")
    status: str = doc_field(
        "PR status: 'open' | 'closed' | 'merged' | 'draft'.",
        default=PRStatus.OPEN,
        display=True,
        filter_arg="status",
    )
    assignee: str | None = doc_field("Assignee GitHub login.")
    author: str | None = doc_field(
        "Author — GitHub login, JIRA name, or email (auto-resolved).",
        display=True,
        filter_arg="author",
        filter_kind="user",
        identity="github.login",
    )
    labels: list[str] = doc_field("PR labels.", default_factory=list)
    base: str | None = doc_field("Base branch.")
    head: str | None = doc_field("Head branch.")
    merged: bool = doc_field("Whether the PR was merged.", default=False, display=True)
    merged_at: str | None = doc_field("ISO-8601 merge timestamp.")
    is_draft: bool = doc_field("Whether the PR is a draft.", default=False)
    comments_count: int = doc_field("Number of review/issue comments.", default=0)
    created: str | None = doc_field("ISO-8601 creation timestamp.")
    updated: str | None = doc_field("ISO-8601 last-updated timestamp.", display=True)
    url: str | None = doc_field("Browser URL for the PR.", display=True)
    jira_keys: list[str] = doc_field(
        "JIRA ticket keys this PR references (from branch/title/body).",
        default_factory=list,
        display=True,
        filter_arg="has_jira_link",
        filter_kind="has_refs",
    )
    text: str | None = doc_field("Embed-ready blob (key + title + body).")
