"""Minimal GitHub REST v3 client: list non-archived repos + paginated PRs.

Mirrors jira_client.py: thin wrapper around requests, returns flattened mongo
docs ready for upsert. Connection details come from `current_github_config()`
so a multi-tenant ingest can rebind creds per tenant via `use_github(cfg)`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from yoku.connectors._runtime import current_github_config
from yoku.log import get_logger
from yoku.utils import extract_jira_keys, make_retry

_retry_log = get_logger("github_client.retry")

BOT_AUTHORS = {
    "dependabot[bot]",
    "coderabbitai[bot]",
    "github-actions[bot]",
    "copilot-pull-request-reviewer[bot]",
    "renovate[bot]",
    "asato-bot",
}


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {current_github_config().token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@make_retry("github", _retry_log)
def _get(url: str, params: dict[str, Any] | None = None) -> requests.Response:
    r = requests.get(url, headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r


def _paginate(url: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
    """Walk all pages of a list endpoint using Link headers."""
    next_url: str | None = url
    next_params = params
    while next_url:
        r = _get(next_url, next_params)
        for item in r.json():
            yield item
        next_url = r.links.get("next", {}).get("url")
        next_params = None  # next link already has query params baked in


def list_non_archived_repos() -> Iterator[dict]:
    """Yield each non-archived repo in the configured org."""
    cfg = current_github_config()
    url = f"{cfg.api_base_clean}/orgs/{cfg.org}/repos"
    for repo in _paginate(url, {"type": "all", "per_page": 100, "sort": "updated"}):
        if repo.get("archived"):
            continue
        yield repo


def list_pulls(repo_full_name: str, since: datetime) -> Iterator[dict]:
    """Yield PRs updated >= `since`. Stops early once older PRs appear.

    GitHub doesn't filter PRs by updated, so we sort by updated DESC and
    short-circuit at the cutoff. A 404/403/410 on the repo is logged and
    skipped so one bad repo doesn't kill an org-wide ingest.
    """
    url = f"{current_github_config().api_base_clean}/repos/{repo_full_name}/pulls"
    params = {
        "state": "all",
        "per_page": 100,
        "sort": "updated",
        "direction": "desc",
    }
    try:
        for pr in _paginate(url, params):
            updated = _parse_dt(pr.get("updated_at"))
            if updated and updated < since:
                return
            yield pr
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (403, 404, 410):
            _retry_log.warning(
                "skip repo=%s status=%s reason=%s",
                repo_full_name,
                code,
                (
                    (e.response.json() or {}).get("message", "unknown")
                    if e.response is not None
                    else "no response"
                ),
            )
            return
        raise


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _derive_status(pr: dict) -> str:
    if pr.get("merged_at"):
        return "merged"
    if pr.get("draft"):
        return "draft"
    return pr.get("state") or "unknown"


def pr_to_doc(pr: dict, repo_full_name: str) -> dict:
    """Flatten a GitHub PR into a mongo doc parallel to jira_tickets."""
    number = pr["number"]
    title = pr.get("title") or ""
    body = pr.get("body") or ""
    head_ref = (pr.get("head") or {}).get("ref") or ""
    base_ref = (pr.get("base") or {}).get("ref") or ""
    author = (pr.get("user") or {}).get("login")
    assignees = pr.get("assignees") or []
    assignee = assignees[0].get("login") if assignees else None
    labels = [l.get("name") for l in (pr.get("labels") or []) if l.get("name")]
    status = _derive_status(pr)
    jira_keys = extract_jira_keys(head_ref, title, body)

    key = f"{repo_full_name}#{number}"
    text = f"[{key}] {title}\n\n{body}".strip()

    return {
        "key": key,
        "repo": repo_full_name,
        "number": number,
        "summary": title,
        "description": body,
        "status": status,
        "assignee": assignee,
        "author": author,
        "labels": labels,
        "base": base_ref,
        "head": head_ref,
        "merged": bool(pr.get("merged_at")),
        "merged_at": pr.get("merged_at"),
        "is_draft": bool(pr.get("draft")),
        "comments_count": pr.get("comments", 0),
        "created": pr.get("created_at"),
        "updated": pr.get("updated_at"),
        "url": pr.get("html_url"),
        "jira_keys": jira_keys,
        "text": text,
    }


def is_bot(pr: dict) -> bool:
    login = (pr.get("user") or {}).get("login") or ""
    if login in BOT_AUTHORS:
        return True
    return login.endswith("[bot]")


def lookback_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(days=current_github_config().pr_lookback_days)
