"""Read-only stats endpoints — used by the UI dashboard."""

from __future__ import annotations

from fastapi import APIRouter

from agent_yoku.deps import CurrentUser
from agent_yoku.schemas import CountsResponse, SourceFreshness
from agent_yoku.storage.freshness import source_freshness
from agent_yoku.storage.mongo import (
    chat_messages_collection,
    chat_sessions_collection,
    github_prs_collection,
    github_users_collection,
    tickets_collection,
    unified_users_collection,
    users_collection,
)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/counts", response_model=CountsResponse)
async def counts(_user: CurrentUser) -> CountsResponse:
    return CountsResponse(
        jira_tickets=tickets_collection().count_documents({}),
        jira_users=users_collection().count_documents({}),
        github_prs=github_prs_collection().count_documents({}),
        github_users=github_users_collection().count_documents({}),
        unified_users=unified_users_collection().count_documents({}),
        chat_sessions=chat_sessions_collection().count_documents({}),
        chat_messages=chat_messages_collection().count_documents({}),
    )


@router.get("/freshness", response_model=list[SourceFreshness])
async def freshness(_user: CurrentUser) -> list[SourceFreshness]:
    """How current each data source is — drives the sidebar 'synced X ago' badge."""
    return [SourceFreshness(**row) for row in source_freshness()]
