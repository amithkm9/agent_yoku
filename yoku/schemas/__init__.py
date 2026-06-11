"""Pydantic schemas for the entities yoku stores in mongo.

Each collection's model is the single source of truth for its description and
fields; the agent's schema registry reads them so nothing about a collection is
hardcoded in the tool layer. They are *not* used to validate every mongo read
(too costly), but available for typed access (tests, exports, future REST API).
"""

from yoku.schemas.directory import GitHubUser, JiraUser, SlackUser
from yoku.schemas.entity_link import EntityLink
from yoku.schemas.github import GitHubPR, PRStatus
from yoku.schemas.jira import JiraTicket, LinkedPR
from yoku.schemas.metric import MetricPoint
from yoku.schemas.session import ChatMessage, ChatSession
from yoku.schemas.slack import SlackMessage
from yoku.schemas.unified import (
    Conversation,
    Primitive,
    PullRequest,
    WorkItem,
)
from yoku.schemas.user import GitHubUserBlock, JiraUserBlock, UnifiedUser

__all__ = [
    "ChatMessage",
    "ChatSession",
    "Conversation",
    "EntityLink",
    "GitHubPR",
    "GitHubUser",
    "GitHubUserBlock",
    "JiraTicket",
    "JiraUser",
    "JiraUserBlock",
    "LinkedPR",
    "MetricPoint",
    "PRStatus",
    "Primitive",
    "PullRequest",
    "SlackMessage",
    "SlackUser",
    "UnifiedUser",
    "WorkItem",
]
