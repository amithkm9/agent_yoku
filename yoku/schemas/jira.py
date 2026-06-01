"""Pydantic schema for JIRA entities stored in mongo (source of truth)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from yoku.schemas._fields import doc_field


class LinkedPR(BaseModel):
    """A PR reference attached to a JIRA ticket's `linked_prs` array."""

    key: str
    repo: str | None = None
    summary: str | None = None
    status: str | None = None
    url: str | None = None
    merged: bool = False


class JiraTicket(BaseModel):
    """Asato JIRA tickets (project AS) — what we plan to do."""

    model_config = ConfigDict(extra="allow")

    key: str = doc_field("Ticket key, e.g. 'AS-4163'.", default=..., display=True)
    summary: str | None = doc_field("Short ticket title.", display=True)
    description: str | None = doc_field("Full ticket description text.")
    status: str | None = doc_field(
        "Workflow status, e.g. 'To Do', 'In Progress', 'Done'.",
        display=True,
        filter_arg="status",
    )
    issuetype: str | None = doc_field(
        "Issue type: 'Task', 'Story', 'Bug', 'Epic'.", display=True, filter_arg="issuetype"
    )
    epic_key: str | None = doc_field(
        "Parent epic key, e.g. 'AS-1000'.", display=True, filter_arg="epic_key"
    )
    parent_key: str | None = doc_field("Parent issue key (for sub-tasks).", display=True)
    assignee: str | None = doc_field(
        "Assignee display name — JIRA name, GitHub login, or email (auto-resolved).",
        display=True,
        filter_arg="assignee",
        filter_kind="user",
        identity="jira.displayName",
    )
    reporter: str | None = doc_field("Reporter display name.")
    priority: str | None = doc_field("Priority name, e.g. 'High'.")
    labels: list[str] = doc_field(
        "Labels, e.g. 'apr-bug-bash'.", default_factory=list, filter_arg="label"
    )
    fix_versions: list[str] = doc_field(
        "Target release names, e.g. 'Sprint 24'.", default_factory=list, filter_arg="fix_version"
    )
    created: str | None = doc_field("ISO-8601 creation timestamp.")
    updated: str | None = doc_field("ISO-8601 last-updated timestamp.", display=True)
    text: str | None = doc_field("Embed-ready blob (key + summary + description).")
    url: str | None = doc_field("Browser URL for the ticket.", display=True)
    linked_prs: list[LinkedPR] = Field(
        default_factory=list,
        description="PRs that reference this ticket (materialised by the linker).",
    )
