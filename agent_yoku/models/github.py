"""Pydantic DTOs for GitHub entities stored in mongo."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PRStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    DRAFT = "draft"


class GitHubPR(BaseModel):
    """Shape of a document in `github_prs`."""

    model_config = ConfigDict(extra="allow")

    key: str  # 'AsatoCorp/agent-svc#173'
    repo: str  # 'AsatoCorp/agent-svc'
    number: int
    summary: str | None = None  # PR title
    description: str | None = None  # PR body
    status: PRStatus | str = PRStatus.OPEN
    assignee: str | None = None
    author: str | None = None
    author_email: str | None = None
    labels: list[str] = Field(default_factory=list)
    base: str | None = None
    head: str | None = None
    merged: bool = False
    merged_at: str | None = None
    is_draft: bool = False
    comments_count: int = 0
    created: str | None = None
    updated: str | None = None
    url: str | None = None
    jira_keys: list[str] = Field(default_factory=list)
    text: str | None = None
