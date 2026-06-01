"""Mappers — project stored source docs into the canonical `UnifiedDoc`.

Add a vendor of an existing kind by writing one mapper and registering it here;
nothing else changes. `SOURCE_MAPPERS` keys are source collection names.
"""

from __future__ import annotations

from yoku.pipeline.mappers.base import Mapper
from yoku.pipeline.mappers.github import map_github_pr
from yoku.pipeline.mappers.jira import map_jira_ticket
from yoku.pipeline.mappers.slack import map_slack_message

#: source collection -> mapper that projects its docs into a UnifiedDoc.
SOURCE_MAPPERS: dict[str, Mapper] = {
    "jira_tickets": map_jira_ticket,
    "github_prs": map_github_pr,
    "slack_messages": map_slack_message,
}

__all__ = [
    "Mapper",
    "SOURCE_MAPPERS",
    "map_github_pr",
    "map_jira_ticket",
    "map_slack_message",
]
