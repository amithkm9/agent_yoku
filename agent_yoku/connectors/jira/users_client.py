"""Minimal JIRA users connector. Same auth/pattern as jira_client.py."""

from __future__ import annotations

from collections.abc import Iterator

from agent_yoku.config import JIRA_BASE_URL
from agent_yoku.connectors.jira.client import _jira_get


def search_users(page_size: int = 200) -> Iterator[dict]:
    """Yield every user in the JIRA instance via /rest/api/3/users/search."""
    url = f"{JIRA_BASE_URL}/rest/api/3/users/search"
    start_at = 0
    while True:
        params = {"query": "", "startAt": start_at, "maxResults": page_size}
        batch = _jira_get(url, params).json()
        if not batch:
            return
        for u in batch:
            yield u
        if len(batch) < page_size:
            return
        start_at += len(batch)


def user_to_doc(user: dict) -> dict:
    """Flatten a JIRA user payload into our mongo doc shape."""
    avatars = user.get("avatarUrls") or {}
    return {
        "accountId": user.get("accountId"),
        "displayName": user.get("displayName"),
        "emailAddress": user.get("emailAddress"),  # may be None depending on token perms
        "active": bool(user.get("active", False)),
        "accountType": user.get("accountType"),  # "atlassian" | "customer" | "app"
        "timeZone": user.get("timeZone"),
        "locale": user.get("locale"),
        "avatarUrl": avatars.get("48x48"),
        "url": (
            f"{JIRA_BASE_URL}/jira/people/{user.get('accountId')}"
            if user.get("accountId")
            else None
        ),
    }
