"""Pydantic DTOs for the entities agent_yoku stores in mongo.

These formalize the doc shapes the connectors produce + the agent consumes.
They are *not* used to validate every mongo read (too costly), but available
for code that wants typed access (tests, exports, future REST API).
"""

from agent_yoku.models.github import GitHubPR, PRStatus
from agent_yoku.models.jira import JiraTicket, LinkedPR
from agent_yoku.models.session import ChatMessage, ChatSession
from agent_yoku.models.user import GitHubUserBlock, JiraUserBlock, UnifiedUser

__all__ = [
    "ChatMessage",
    "ChatSession",
    "GitHubPR",
    "GitHubUserBlock",
    "JiraTicket",
    "JiraUserBlock",
    "LinkedPR",
    "PRStatus",
    "UnifiedUser",
]
