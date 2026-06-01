"""Read-only stats endpoints — used by the UI dashboard."""

from __future__ import annotations

from fastapi import APIRouter

from yoku.api.schemas import CountsResponse, SourceFreshness
from yoku.core.auth import CurrentUser
from yoku.core.storage.freshness import source_freshness
from yoku.core.storage.mongo import (
    chat_messages_collection,
    chat_sessions_collection,
    dc_github_collection,
    dc_github_users_collection,
    dc_jira_collection,
    dc_jira_users_collection,
    dc_slack_collection,
    dc_slack_users_collection,
    ds_unified_users_collection,
)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/counts", response_model=CountsResponse)
async def counts(_user: CurrentUser) -> CountsResponse:
    return CountsResponse(
        jira_tickets=dc_jira_collection().count_documents({}),
        jira_users=dc_jira_users_collection().count_documents({}),
        github_prs=dc_github_collection().count_documents({}),
        github_users=dc_github_users_collection().count_documents({}),
        slack_messages=dc_slack_collection().count_documents({}),
        slack_users=dc_slack_users_collection().count_documents({}),
        unified_users=ds_unified_users_collection().count_documents({}),
        chat_sessions=chat_sessions_collection().count_documents({}),
        chat_messages=chat_messages_collection().count_documents({}),
    )


@router.get("/freshness", response_model=list[SourceFreshness])
async def freshness(_user: CurrentUser) -> list[SourceFreshness]:
    """How current each data source is — drives the sidebar 'synced X ago' badge."""
    return [SourceFreshness(**row) for row in source_freshness()]
