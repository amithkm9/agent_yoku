"""Mappers — project stored source docs into the canonical `UnifiedDoc`.

Add a vendor of an existing kind by writing one mapper and registering it here;
nothing else changes. `SOURCE_MAPPERS` keys are source collection names.
"""

from __future__ import annotations

from yoku.mappers.base import Mapper
from yoku.mappers.github import map_github_pr
from yoku.mappers.jira import map_jira_ticket
from yoku.mappers.slack import map_slack_message

#: source collection -> mapper that projects its docs into a UnifiedDoc.
SOURCE_MAPPERS: dict[str, Mapper] = {
    "dc-jira": map_jira_ticket,
    "dc-github": map_github_pr,
    "dc-slack": map_slack_message,
}

__all__ = [
    "Mapper",
    "SOURCE_MAPPERS",
    "map_github_pr",
    "map_jira_ticket",
    "map_slack_message",
]
