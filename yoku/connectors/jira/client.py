"""Minimal JIRA REST v3 client: paginated search + ADF -> plain text.

Reads connection details from `current_jira_config()` on every call so a
multi-tenant ingest can rebind creds per tenant via `use_jira(cfg)`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import requests

from yoku.connectors._runtime import current_jira_config
from yoku.log import get_logger
from yoku.utils import make_retry

_retry_log = get_logger("jira_client.retry")


@make_retry("jira", _retry_log)
def _jira_get(url: str, params: dict[str, Any]) -> requests.Response:
    cfg = current_jira_config()
    r = requests.get(url, params=params, auth=(cfg.email, cfg.token), timeout=30)
    r.raise_for_status()
    return r


_FIELDS = [
    "summary",
    "description",
    "status",
    "issuetype",
    "assignee",
    "reporter",
    "priority",
    "labels",
    "created",
    "updated",
    "fixVersions",
    "parent",
]


def adf_to_text(node: Any) -> str:
    """Walk an Atlassian Document Format tree and join all text leaves."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(filter(None, (adf_to_text(n) for n in node)))
    if isinstance(node, dict):
        parts: list[str] = []
        if node.get("type") == "text" and "text" in node:
            parts.append(node["text"])
        if "content" in node:
            parts.append(adf_to_text(node["content"]))
        return "\n".join(filter(None, parts))
    return ""


def search_issues(jql: str, page_size: int = 100) -> Iterator[dict]:
    """Yield issues page by page using the new /search/jql endpoint."""
    url = f"{current_jira_config().base_url_clean}/rest/api/3/search/jql"
    next_token: str | None = None
    while True:
        params: dict[str, Any] = {
            "jql": jql,
            "fields": ",".join(_FIELDS),
            "maxResults": page_size,
        }
        if next_token:
            params["nextPageToken"] = next_token
        r = _jira_get(url, params)
        data = r.json()
        for issue in data.get("issues", []):
            yield issue
        if data.get("isLast", True):
            return
        next_token = data.get("nextPageToken")
        if not next_token:
            return


def _parent_links(parent: dict | None) -> tuple[str | None, str | None]:
    """Derive (epic_key, parent_key) from a team-managed issue's `parent` field.

    `parent` points one level up: an Epic for a Story/Task, or a Story/Task for
    a Sub-task. We always record `parent_key`; `epic_key` is set only when the
    immediate parent is itself an Epic. A Sub-task's epic is resolved at query
    time by joining through its parent Story's `epic_key`.
    """
    if not parent:
        return None, None
    parent_key = parent.get("key")
    parent_type = ((parent.get("fields") or {}).get("issuetype") or {}).get("name")
    epic_key = parent_key if parent_type == "Epic" else None
    return epic_key, parent_key


def issue_to_doc(issue: dict) -> dict:
    """Flatten a JIRA issue into a mongo doc with a clean text blob."""
    f = issue.get("fields", {})
    description = adf_to_text(f.get("description"))
    summary = f.get("summary") or ""
    status = (f.get("status") or {}).get("name")
    issuetype = (f.get("issuetype") or {}).get("name")
    epic_key, parent_key = _parent_links(f.get("parent"))
    assignee = (f.get("assignee") or {}).get("displayName")
    reporter = (f.get("reporter") or {}).get("displayName")
    priority = (f.get("priority") or {}).get("name")

    text = f"[{issue['key']}] {summary}\n\n{description}".strip()

    return {
        "key": issue["key"],
        "summary": summary,
        "description": description,
        "status": status,
        "issuetype": issuetype,
        "epic_key": epic_key,
        "parent_key": parent_key,
        "assignee": assignee,
        "reporter": reporter,
        "priority": priority,
        "labels": f.get("labels") or [],
        "fix_versions": [v.get("name") for v in (f.get("fixVersions") or []) if v.get("name")],
        "created": f.get("created"),
        "updated": f.get("updated"),
        "text": text,
        "url": f"{current_jira_config().base_url_clean}/browse/{issue['key']}",
    }
