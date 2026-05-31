"""Pydantic schemas for the entities yoku stores in mongo.

Each collection's model is the single source of truth for its description and
fields; the agent's schema registry reads them so nothing about a collection is
hardcoded in the tool layer. They are *not* used to validate every mongo read
(too costly), but available for typed access (tests, exports, future REST API).
"""

from yoku.models.directory import GitHubUser, JiraUser, SlackUser
from yoku.models.github import GitHubPR, PRStatus
from yoku.models.jira import JiraTicket, LinkedPR
from yoku.models.session import ChatMessage, ChatSession
from yoku.models.slack import SlackMessage
from yoku.models.user import GitHubUserBlock, JiraUserBlock, UnifiedUser

__all__ = [
    "ChatMessage",
    "ChatSession",
    "GitHubPR",
    "GitHubUser",
    "GitHubUserBlock",
    "JiraTicket",
    "JiraUser",
    "JiraUserBlock",
    "LinkedPR",
    "PRStatus",
    "SlackMessage",
    "SlackUser",
    "UnifiedUser",
]
