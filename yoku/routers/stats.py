"""Read-only stats endpoints — used by the UI dashboard."""

from __future__ import annotations

from fastapi import APIRouter

from yoku.auth import CurrentUser
from yoku.db.freshness import source_freshness
from yoku.db.mongo import (
    chat_messages_collection,
    chat_sessions_collection,
    dc_github_collection,
    dc_github_users_collection,
    dc_jira_collection,
    dc_jira_users_collection,
    dc_slack_collection,
    dc_slack_users_collection,
    ds_metrics_collection,
    ds_unified_users_collection,
)
from yoku.schemas.api import CountsResponse, SourceFreshness, TrendPoint, TrendsResponse

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


@router.get("/trends", response_model=TrendsResponse)
async def trends(_user: CurrentUser, weeks: int = 12) -> TrendsResponse:
    """Weekly trend series from ds-metrics — drives the Trends dashboard.

    Returns the trailing `weeks` of every computed metric, sparse weeks
    omitted per series (the UI fills gaps with zeros for count metrics).
    """
    weeks = max(4, min(weeks, 52))
    rows = list(
        ds_metrics_collection()
        .find({}, {"_id": 0, "metric": 1, "week": 1, "value": 1, "n": 1})
        .sort([("week", 1)])
    )
    all_weeks = sorted({r["week"] for r in rows})[-weeks:]
    keep = set(all_weeks)
    series: dict[str, list[TrendPoint]] = {}
    for r in rows:
        if r["week"] in keep:
            series.setdefault(r["metric"], []).append(
                TrendPoint(week=r["week"], value=r["value"], n=r.get("n"))
            )
    return TrendsResponse(weeks=all_weeks, series=series)
