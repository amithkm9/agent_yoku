"""Pydantic DTOs for chat session persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatSession(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    title: str | None = None
    created_at: datetime
    last_active_at: datetime
    turn_count: int = 0


class ChatMessage(BaseModel):
    """A single persisted agent message (human / ai / tool)."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    turn_id: str
    turn_seq: int
    msg_idx: int
    role: Literal["human", "ai", "tool", "system"]
    content: Any  # str or list of structured blocks (OpenAI Responses API)
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
