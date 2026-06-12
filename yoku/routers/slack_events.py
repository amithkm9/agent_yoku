"""Slack Events API endpoint — yoku's ears (Phase 4b).

POST /api/slack/events receives DMs to the bot and @mentions. Unlike every
other route, the caller is Slack, not a logged-in user, so security is:

1. **Tenant routing** — the event's `team_id` is matched against each
   tenant's stored Slack `team_id` (cached lookup across tenant dbs).
2. **Signature verification** — Slack's v0 HMAC over the raw body with the
   tenant's signing secret, with a replay window on the timestamp. Events for
   tenants without a signing secret are rejected (the bot voice is opt-in).

Verified DM / app_mention events land in `slack_inbound` (deduped by Slack's
event_id) for Phase 5's conversation loop. The bot's own messages are ignored
so yoku can never converse with itself.

`url_verification` (Slack's one-time endpoint handshake) is answered before
tenant routing — it carries no team data and only echoes Slack's challenge.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from yoku.db import connector_configs as cc
from yoku.db import tenancy
from yoku.db.mongo import slack_inbound_collection
from yoku.logging import get_logger

router = APIRouter(prefix="/slack", tags=["slack"])
log = get_logger("slack_events")

_REPLAY_WINDOW_S = 60 * 5
_TEAM_CACHE_TTL_S = 300

# team_id -> (tenant_id, signing_secret, bot_user_id?) resolved at most every TTL.
_team_cache: dict[str, tuple[str, str]] = {}
_team_cache_at: float = 0.0


def _find_tenant_for_team(team_id: str) -> tuple[str, str] | None:
    """Map a Slack team id to (tenant_id, signing_secret). Cached across calls."""
    global _team_cache, _team_cache_at
    now = time.monotonic()
    if team_id in _team_cache and (now - _team_cache_at) < _TEAM_CACHE_TTL_S:
        return _team_cache[team_id]

    from yoku.pipeline.sync_service import list_tenant_ids

    fresh: dict[str, tuple[str, str]] = {}
    for tenant_id in list_tenant_ids():
        tenancy.set_tenant(tenant_id)
        decrypted = cc.get_config_decrypted("slack") or {}
        cfg = (cc.get_config("slack") or {}).get("config") or {}
        t_id = cfg.get("team_id")
        secret = decrypted.get("signing_secret")
        if t_id and secret:
            fresh[t_id] = (tenant_id, secret)
    tenancy.set_tenant(None)  # the handler binds the routed tenant explicitly
    _team_cache = fresh
    _team_cache_at = now
    return _team_cache.get(team_id)


def _verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Slack v0 signing: HMAC-SHA256 of 'v0:<ts>:<body>' with the signing secret."""
    try:
        if abs(time.time() - float(timestamp)) > _REPLAY_WINDOW_S:
            return False
    except (TypeError, ValueError):
        return False
    base = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _is_bot_echo(event: dict) -> bool:
    """True for the bot's own messages and other non-human noise."""
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return True
    return not event.get("user")


@router.post("/events")
async def slack_events(request: Request) -> dict:
    body = await request.body()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from e

    # One-time endpoint handshake — no team context yet, just echo.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    team_id = payload.get("team_id")
    if not team_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing team_id")
    routed = _find_tenant_for_team(team_id)
    if routed is None:
        # Unknown workspace — don't reveal whether the endpoint is live for others.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown team")
    tenant_id, secret = routed

    if not _verify_signature(
        secret,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        body,
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")

    if payload.get("type") != "event_callback":
        return {"ok": True}
    event = payload.get("event") or {}
    etype = event.get("type")
    is_dm = etype == "message" and event.get("channel_type") == "im"
    if not (is_dm or etype == "app_mention") or _is_bot_echo(event):
        return {"ok": True}

    tenancy.set_tenant(tenant_id)
    doc = {
        "event_id": payload.get("event_id"),
        "team_id": team_id,
        "event_type": "dm" if is_dm else "mention",
        "channel": event.get("channel"),
        "slack_user_id": event.get("user"),
        "text": (event.get("text") or "").strip(),
        "ts": event.get("ts"),
        "thread_ts": event.get("thread_ts"),
        "received_at": datetime.now(UTC),
        "processed": False,
    }
    try:
        slack_inbound_collection().insert_one(doc)
    except Exception:
        # Duplicate event_id — Slack retries deliveries; first write wins.
        log.info("slack inbound duplicate event_id=%s", payload.get("event_id"))
    else:
        log.info(
            "slack inbound %s from %s tenant=%s", doc["event_type"], doc["slack_user_id"], tenant_id
        )
    return {"ok": True}
