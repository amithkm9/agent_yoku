"""Pydantic request/response models for the FastAPI surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------- Auth ----------


class LoginRequest(BaseModel):
    email: str
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


# ---------- Sessions ----------


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


# ---------- Chat ----------


class ChatRequest(BaseModel):
    session_id: str
    query: str


class ToolCallSummary(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_preview: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    turn_id: str
    answer: str
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)


# ---------- Connectors ----------


class JiraConfigIn(BaseModel):
    """Inbound JIRA config. `token` is optional on edit — blank means
    'keep the previously stored token'. Required on first-time setup."""

    base_url: str
    email: str
    token: str | None = None
    project: str


class GithubConfigIn(BaseModel):
    """Inbound GitHub config. See `JiraConfigIn.token` for blank-token semantics."""

    api_base: str = "https://api.github.com"
    token: str | None = None
    org: str
    pr_lookback_days: int = 365


class ConnectorStatus(BaseModel):
    name: str
    configured: bool
    config: dict[str, Any] = Field(default_factory=dict)  # non-secret fields only
    last_synced_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    updated_at: datetime | None = None


class ConnectorSyncResponse(BaseModel):
    name: str
    status: Literal["started"]


# ---------- Stats ----------


class CountsResponse(BaseModel):
    jira_tickets: int
    jira_users: int
    github_prs: int
    github_users: int
    unified_users: int
    chat_sessions: int
    chat_messages: int


# Forward refs
TokenResponse.model_rebuild()
