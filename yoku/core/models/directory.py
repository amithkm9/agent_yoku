"""Pydantic schemas for the per-source user directory collections.

These aren't agent-filterable sources; they back `resolve_user` / `who_knows`
and the identity cross-walk. Modelled so their descriptions and fields surface
through the schema registry like every other collection.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from yoku.core.models._fields import doc_field


class JiraUser(BaseModel):
    """JIRA users directory — one row per Atlassian account."""

    model_config = ConfigDict(extra="allow")

    accountId: str | None = doc_field("Atlassian account id.")
    displayName: str | None = doc_field("User display name.")
    emailAddress: str | None = doc_field("Email, if the token may read it.")
    active: bool = doc_field("Whether the account is active.", default=True)
    accountType: str | None = doc_field("'atlassian' | 'customer' | 'app'.")
    timeZone: str | None = doc_field("User time zone.")
    locale: str | None = doc_field("User locale.")
    avatarUrl: str | None = doc_field("Avatar image URL.")
    url: str | None = doc_field("Profile URL.")


class GitHubUser(BaseModel):
    """AsatoCorp GitHub org members directory."""

    model_config = ConfigDict(extra="allow")

    login: str = doc_field("GitHub login.", default=...)
    id: int | None = doc_field("GitHub numeric user id.")
    name: str | None = doc_field("Full name, if public.")
    email: str | None = doc_field("Email, if public.")
    type: str | None = doc_field("'User' | 'Bot'.")
    is_bot: bool = doc_field("Whether the account is a bot.", default=False)
    company: str | None = doc_field("Company field from the profile.")
    blog: str | None = doc_field("Blog/website from the profile.")
    avatar_url: str | None = doc_field("Avatar image URL.")
    html_url: str | None = doc_field("Profile URL.")


class SlackUser(BaseModel):
    """Slack workspace users directory."""

    model_config = ConfigDict(extra="allow")

    user_id: str = doc_field("Slack user id.", default=...)
    name: str | None = doc_field("Slack handle.")
    display_name: str | None = doc_field("Display name (or real name fallback).")
    real_name: str | None = doc_field("Real name.")
    email: str | None = doc_field("Email, if exposed.")
    is_bot: bool = doc_field("Whether the account is a bot.", default=False)
