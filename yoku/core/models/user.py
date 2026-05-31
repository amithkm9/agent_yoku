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
    """Cross-walk between JIRA and GitHub users — one row per person.

    Either `jira` or `github` (or both) is populated; `match_source` records how
    the join was made. Backs alias resolution for person-valued filters.
    """

    model_config = ConfigDict(extra="allow")

    user_id: str
    email: str | None = None
    jira: JiraUserBlock | None = None
    github: GitHubUserBlock | None = None
    is_bot: bool = False
    match_source: Literal["email", "name", "manual", "jira_only", "github_only"] = "jira_only"
