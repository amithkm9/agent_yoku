"""Minimal Slack Web API client: list channels, paginate message history, list users.

Uses requests directly (no slack_sdk dependency) following the same pattern as
jira/client.py and github/client.py. All creds come from current_slack_config()
so multi-tenant ingest can rebind per tenant via use_slack(cfg).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from yoku.core.logging import get_logger
from yoku.core.utils import extract_jira_keys, make_retry
from yoku.pipeline.connectors._runtime import current_slack_config

_retry_log = get_logger("slack_client.retry")

_BASE = "https://slack.com/api"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {current_slack_config().bot_token}"}


@make_retry("slack", _retry_log)
def _get(endpoint: str, params: dict[str, Any] | None = None) -> dict:
    r = requests.get(f"{_BASE}/{endpoint}", headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise requests.HTTPError(f"Slack API error: {data.get('error', 'unknown')}")
    return data


def _paginate(endpoint: str, list_key: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
    """Walk all cursor-paginated pages of a Slack list endpoint."""
    p = dict(params or {})
    p.setdefault("limit", 200)
    while True:
        data = _get(endpoint, p)
        yield from data.get(list_key, [])
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
        p["cursor"] = cursor


def list_channels(channel_types: str = "public_channel") -> Iterator[dict]:
    """Yield non-archived channels of the configured type(s)."""
    for ch in _paginate("conversations.list", "channels", {"types": channel_types}):
        if not ch.get("is_archived"):
            yield ch


def join_channel(channel_id: str) -> bool:
    """Join a public channel so the bot can read its history.

    Returns True if joined/already a member, False if the scope is missing
    or the channel type doesn't support joining. Raises on other errors.
    """
    try:
        _get("conversations.join", {"channel": channel_id})
        return True
    except requests.HTTPError as exc:
        msg = str(exc)
        if "already_in_channel" in msg:
            return True
        if "missing_scope" in msg or "method_not_supported_for_channel_type" in msg:
            return False
        raise


def list_messages(channel_id: str, oldest: float) -> Iterator[dict]:
    """Yield messages from a channel newer than `oldest` (unix timestamp).

    Only yields actual user messages (type=message, user present, non-empty text).
    Bot messages and system events (join/leave, file uploads without text) are skipped.
    """
    params: dict[str, Any] = {"channel": channel_id, "oldest": str(oldest), "limit": 200}
    for msg in _paginate("conversations.history", "messages", params):
        if msg.get("type") != "message":
            continue
        if not msg.get("user"):
            continue
        if not (msg.get("text") or "").strip():
            continue
        yield msg


def list_users() -> Iterator[dict]:
    """Yield non-deleted workspace members (humans only)."""
    for u in _paginate("users.list", "members"):
        if u.get("deleted") or u.get("is_bot") or u.get("id") == "USLACKBOT":
            continue
        yield u


def fetch_user_map() -> dict[str, dict]:
    """Return {user_id: {display_name, email}} for all non-deleted humans.

    Called once per ingest run to resolve message author IDs to display names
    without per-message API calls.
    """
    out: dict[str, dict] = {}
    for u in list_users():
        uid = u.get("id")
        if not uid:
            continue
        profile = u.get("profile") or {}
        out[uid] = {
            "display_name": profile.get("display_name")
            or profile.get("real_name")
            or u.get("name")
            or uid,
            "email": (profile.get("email") or "").lower() or None,
        }
    return out


def lookback_oldest() -> float:
    """Unix timestamp for now minus configured lookback_days."""
    cfg = current_slack_config()
    cutoff = datetime.now(UTC) - timedelta(days=cfg.lookback_days)
    return cutoff.timestamp()


def message_to_doc(
    msg: dict,
    channel_id: str,
    channel_name: str,
    user_map: dict[str, dict],
    workspace: str,
) -> dict:
    """Flatten a Slack message dict into a mongo doc."""
    ts = msg["ts"]
    user_id = msg.get("user") or ""
    user_info = user_map.get(user_id) or {}
    author_name = user_info.get("display_name") or user_id
    author_email = user_info.get("email")

    text = (msg.get("text") or "").strip()
    thread_ts = msg.get("thread_ts")
    is_reply = bool(thread_ts and thread_ts != ts)

    reactions = msg.get("reactions") or []
    reactions_count = sum(r.get("count", 0) for r in reactions)

    # key uses raw ts so it's sortable and unique within a channel
    key = f"{channel_id}/{ts}"

    # embed-ready text gives the model channel + author context
    embed_text = f"[#{channel_name}] {author_name}: {text}"

    ts_iso = datetime.fromtimestamp(float(ts), tz=UTC).isoformat()

    # Slack archive URL uses ts without the dot, prefixed with 'p'
    ts_url = ts.replace(".", "")
    url = f"https://{workspace}.slack.com/archives/{channel_id}/p{ts_url}"

    return {
        "key": key,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "author_id": user_id,
        "author_name": author_name,
        "author_email": author_email,
        "summary": text[:200],
        "text": embed_text,
        "ts": ts,
        "thread_ts": thread_ts,
        "is_thread_reply": is_reply,
        "reply_count": msg.get("reply_count") or 0,
        "reactions_count": reactions_count,
        "jira_keys": extract_jira_keys(text),
        "created": ts_iso,
        "updated": ts_iso,
        "url": url,
        "source": "slack",
    }
