"""Pydantic DTOs for JIRA entities stored in mongo."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LinkedPR(BaseModel):
    """A PR reference attached to a JIRA ticket's `linked_prs` array."""

    key: str
    repo: str | None = None
    summary: str | None = None
    status: str | None = None
    url: str | None = None
    merged: bool = False


class JiraTicket(BaseModel):
    """Shape of a document in `jira_tickets`."""

    model_config = ConfigDict(extra="allow")

    key: str
    summary: str | None = None
    description: str | None = None
    status: str | None = None
    issuetype: str | None = None
    assignee: str | None = None
    reporter: str | None = None
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    fix_versions: list[str] = Field(default_factory=list)
    created: str | None = None
    updated: str | None = None
    text: str | None = None
    url: str | None = None
    linked_prs: list[LinkedPR] = Field(default_factory=list)
