"""Pydantic DTOs for user entities (JIRA, GitHub, unified)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class JiraUserBlock(BaseModel):
    accountId: str | None = None
    displayName: str | None = None
    emailAddress: str | None = None
    active: bool = True


class GitHubUserBlock(BaseModel):
    login: str
    id: int | None = None
    name: str | None = None
    email: str | None = None
    is_bot: bool = False


class UnifiedUser(BaseModel):
    """Shape of a document in `unified_users`. Either jira or github (or both)
    populated; `match_source` describes how the join was made.
    """

    model_config = ConfigDict(extra="allow")

    user_id: str
    email: str | None = None
    jira: JiraUserBlock | None = None
    github: GitHubUserBlock | None = None
    is_bot: bool = False
    match_source: Literal["email", "name", "manual", "jira_only", "github_only"] = "jira_only"
