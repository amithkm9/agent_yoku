"""Pydantic request/response models for the FastAPI surface."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# GitHub org/login slugs: alphanumerics joined by single hyphens, max 39 chars.
# Mirrors GitHub's own account-name rules so we reject values that can only 404.
_GITHUB_ORG_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_GITHUB_ORG_MAX_LEN = 39


def normalize_github_org(raw: str) -> str:
    """Reduce a pasted GitHub org reference down to its bare slug.

    Accepts the slug itself plus the common things people paste instead:
    a full profile URL (``https://github.com/AsatoCorp``), the org page
    (``github.com/orgs/AsatoCorp``), an ``@AsatoCorp`` handle, or any of the
    above with surrounding whitespace / trailing slashes. Returns ``""`` when
    nothing slug-like remains so the caller can raise a clear error.
    """
    value = raw.strip()
    lowered = value.lower()
    if "github.com" in lowered:
        # Drop scheme + host, keep only the path.
        value = value[lowered.index("github.com") + len("github.com") :]
    value = value.lstrip("@/")
    segments = [s for s in value.split("/") if s]
    if not segments:
        return ""
    # GitHub org pages live under /orgs/<slug>; unwrap that one extra segment.
    if segments[0].lower() == "orgs" and len(segments) > 1:
        return segments[1]
    return segments[0]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    tenant_id: str
    is_admin: bool = False
    # Tenant's JIRA site, for citation links in the UI. Only populated by /me.
    jira_base_url: str | None = None


class SignalOut(BaseModel):
    """A matured proactive gap, as shown in the Inbox."""

    signal_id: str
    detector: str
    kind: str
    item_key: str
    title: str | None = None
    person_name: str | None = None
    evidence: dict = {}
    confidence: float = 0.5
    url: str | None = None
    status: str
    label: str | None = None
    first_seen_at: datetime | None = None
    matured_at: datetime | None = None
    last_seen_at: datetime | None = None


class InboxResponse(BaseModel):
    signals: list[SignalOut]
    total_open: int
    total_matured: int


class TrendPoint(BaseModel):
    week: str  # ISO date of the week's Monday
    value: float
    n: int | None = None


class TrendsResponse(BaseModel):
    weeks: list[str]
    series: dict[str, list[TrendPoint]]


class SessionSummary(BaseModel):
    session_id: str
    title: str | None
    created_at: datetime
    last_active_at: datetime
    turn_count: int


class SessionDetail(SessionSummary):
    messages: list[dict] = Field(default_factory=list)


class CreateSessionResponse(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    query: str = Field(min_length=1, max_length=4000)


class ToolCallSummary(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_preview: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    turn_id: str
    answer: str
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)


class JiraConfigIn(BaseModel):
    """Inbound JIRA config. `token` is optional on edit — blank means
    'keep the previously stored token'. Required on first-time setup."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    email: str
    token: str | None = None
    project: str


class GithubConfigIn(BaseModel):
    """Inbound GitHub config. See `JiraConfigIn.token` for blank-token semantics."""

    model_config = ConfigDict(extra="forbid")

    api_base: str = "https://api.github.com"
    token: str | None = None
    org: str = Field(
        examples=["AsatoCorp"],
        description="GitHub organization slug (not the full URL), e.g. 'AsatoCorp'.",
    )
    pr_lookback_days: int = 365

    @field_validator("org")
    @classmethod
    def _normalize_org(cls, value: str) -> str:
        org = normalize_github_org(value)
        if not org:
            raise ValueError("GitHub organization is required.")
        if len(org) > _GITHUB_ORG_MAX_LEN or not _GITHUB_ORG_RE.match(org):
            raise ValueError(
                f"{value!r} is not a valid GitHub organization. "
                "Enter just the org slug, e.g. 'AsatoCorp'."
            )
        return org


_VALID_CHANNEL_TYPES = frozenset({"public_channel", "private_channel", "mpim", "im"})


def normalize_slack_workspace(raw: str) -> str:
    """Extract bare workspace slug from whatever the user pastes.

    Accepts: 'acme', 'acme.slack.com', 'https://acme.slack.com', 'https://acme.slack.com/'.
    Returns '' when nothing slug-like remains.
    """
    value = raw.strip().rstrip("/")
    # strip scheme
    for prefix in ("https://", "http://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
    # strip .slack.com suffix
    if value.lower().endswith(".slack.com"):
        value = value[: -len(".slack.com")]
    # take only the first path segment (ignore any trailing path)
    value = value.split("/")[0].split(".")[0]
    return value


class SlackConfigIn(BaseModel):
    """Inbound Slack config. `bot_token` is optional on edit — blank means keep existing."""

    model_config = ConfigDict(extra="forbid")

    workspace: str
    bot_token: str | None = None
    lookback_days: int = 90
    channel_types: str = "public_channel"

    @field_validator("workspace")
    @classmethod
    def _normalize_workspace(cls, value: str) -> str:
        slug = normalize_slack_workspace(value)
        if not slug:
            raise ValueError("workspace must be a Slack workspace slug, e.g. 'acme'")
        return slug

    @field_validator("channel_types")
    @classmethod
    def _validate_channel_types(cls, value: str) -> str:
        parts = [p.strip() for p in value.split(",") if p.strip()]
        invalid = [p for p in parts if p not in _VALID_CHANNEL_TYPES]
        if invalid:
            raise ValueError(
                f"Invalid channel type(s): {invalid}. "
                f"Valid values: {sorted(_VALID_CHANNEL_TYPES)}"
            )
        return ",".join(parts)


class ConnectorGuide(BaseModel):
    source: str
    display_name: str
    description: str
    sync_summary: str | None = None
    setup_steps: list[str] = Field(default_factory=list)
    field_help: dict[str, str] = Field(default_factory=dict)


class ConnectorStatus(BaseModel):
    name: str
    guide: ConnectorGuide
    configured: bool
    config: dict[str, Any] = Field(default_factory=dict)  # non-secret fields only
    last_synced_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    updated_at: datetime | None = None


class ConnectorSyncResponse(BaseModel):
    name: str
    status: Literal["started"]


class CountsResponse(BaseModel):
    jira_tickets: int
    jira_users: int
    github_prs: int
    github_users: int
    slack_messages: int
    slack_users: int
    unified_users: int
    chat_sessions: int
    chat_messages: int


class SourceFreshness(BaseModel):
    source: str
    count: int
    last_synced_at: datetime | None = None
    synced_ago: str | None = None
    last_sync_status: str | None = None
