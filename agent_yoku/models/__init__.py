"""Pydantic schemas for the entities agent_yoku stores in mongo.

Each collection's model is the single source of truth for its description and
fields; the agent's schema registry reads them so nothing about a collection is
hardcoded in the tool layer. They are *not* used to validate every mongo read
(too costly), but available for typed access (tests, exports, future REST API).
"""

from agent_yoku.models.directory import GitHubUser, JiraUser, SlackUser
from agent_yoku.models.github import GitHubPR, PRStatus
from agent_yoku.models.jira import JiraTicket, LinkedPR
from agent_yoku.models.session import ChatMessage, ChatSession
from agent_yoku.models.slack import SlackMessage
from agent_yoku.models.user import GitHubUserBlock, JiraUserBlock, UnifiedUser

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
