"""Pydantic schema for Slack entities stored in mongo (source of truth)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from yoku.models._fields import doc_field


class SlackMessage(BaseModel):
    """Slack messages — what the team is discussing across channels."""

    model_config = ConfigDict(extra="allow")

    key: str = doc_field(
        "Message key 'channel_id/ts', e.g. 'C0AB/1700000000.123'.", default=..., display=True
    )
    channel_id: str | None = doc_field("Slack channel id.")
    channel_name: str | None = doc_field(
        "Channel name without '#', e.g. 'engineering'.", display=True, filter_arg="channel"
    )
    author_id: str | None = doc_field("Slack user id of the author.")
    author_name: str | None = doc_field(
        "Author display name — name, login, or email (auto-resolved).",
        display=True,
        filter_arg="author",
        filter_kind="user",
        identity="slack.display_name",
    )
    author_email: str | None = doc_field("Author email, if exposed by Slack.")
    summary: str | None = doc_field("First ~200 chars of the message.", display=True)
    text: str | None = doc_field("Embed-ready blob ([#channel] author: text).")
    ts: str | None = doc_field("Raw Slack timestamp.")
    thread_ts: str | None = doc_field("Parent thread timestamp, if a reply.", display=True)
    is_thread_reply: bool = doc_field(
        "Whether this message is a thread reply.", default=False, display=True
    )
    reply_count: int = doc_field("Number of thread replies.", default=0)
    reactions_count: int = doc_field("Total reaction count.", default=0)
    jira_keys: list[str] = doc_field(
        "JIRA ticket keys mentioned in the message.",
        default_factory=list,
        display=True,
        filter_arg="has_jira_link",
        filter_kind="has_refs",
    )
    created: str | None = doc_field("ISO-8601 message timestamp.")
    updated: str | None = doc_field("ISO-8601 message timestamp.", display=True)
    url: str | None = doc_field("Slack archive URL for the message.", display=True)
