"""Ingest a Slack workspace export ZIP or directory into mongo.

Slack exports (Admin → Settings → Import/export data) produce a folder or ZIP
containing channels.json, users.json, and one subdirectory per channel with
daily JSON files of messages.

Usage (CLI):
    agent-yoku ingest slack-export /path/to/export        # directory
    agent-yoku ingest slack-export /path/to/export.zip    # zip

This writes to the same slack_messages + slack_users collections as the
live API connector, so embed + search work identically after import.
"""

from __future__ import annotations

import json
import re
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo import UpdateOne

from agent_yoku.constants import INGEST_BATCH_SIZE
from agent_yoku.log import get_logger
from agent_yoku.storage.mongo import slack_messages_collection, slack_users_collection
from agent_yoku.utils import extract_jira_keys

log = get_logger("slack_export_ingest")

# Subtypes that are system events, not real messages.
_SKIP_SUBTYPES = {
    "channel_join",
    "channel_leave",
    "channel_purpose",
    "channel_topic",
    "channel_name",
    "channel_archive",
    "channel_unarchive",
    "bot_add",
    "bot_remove",
    "app_install",
    "app_uninstall",
    "pinned_item",
    "unpinned_item",
    "file_share",
}

_SLACK_URL_RE = re.compile(r"<(https?://[^|>]+)(?:\|[^>]*)?>")
_USER_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


def _clean_text(text: str) -> str:
    """Strip Slack mrkdwn markup to plain text for embedding."""
    text = _SLACK_URL_RE.sub(lambda m: m.group(1), text)
    text = _USER_MENTION_RE.sub("", text)
    return text.strip()


def _build_user_map(users_data: list[dict]) -> dict[str, dict]:
    """Build {user_id: {display_name, email}} from users.json."""
    out: dict[str, dict] = {}
    for u in users_data:
        uid = u.get("id")
        if not uid or u.get("is_bot") or u.get("deleted"):
            continue
        profile = u.get("profile") or {}
        out[uid] = {
            "display_name": (
                profile.get("display_name") or profile.get("real_name") or u.get("name") or uid
            ),
            "email": (profile.get("email") or "").lower() or None,
        }
    return out


def _build_channel_map(channels_data: list[dict]) -> dict[str, str]:
    """Build {channel_name: channel_id} from channels.json."""
    return {ch["name"]: ch["id"] for ch in channels_data if ch.get("name") and ch.get("id")}


def _message_to_doc(
    msg: dict,
    channel_id: str,
    channel_name: str,
    user_map: dict[str, dict],
    workspace: str,
) -> dict | None:
    """Convert an export message dict to a mongo doc. Returns None to skip."""
    if msg.get("type") != "message":
        return None
    subtype = msg.get("subtype")
    if subtype in _SKIP_SUBTYPES:
        return None
    if msg.get("bot_id") and not msg.get("user"):
        return None

    user_id = msg.get("user") or ""
    if not user_id:
        return None

    raw_text = (msg.get("text") or "").strip()
    if not raw_text:
        return None

    clean = _clean_text(raw_text)

    # Resolve author: prefer inline user_profile (always present in exports),
    # fall back to the workspace user map.
    inline = msg.get("user_profile") or {}
    user_info = user_map.get(user_id) or {}
    author_name = (
        inline.get("display_name")
        or inline.get("real_name")
        or user_info.get("display_name")
        or user_id
    )
    author_email = user_info.get("email")

    ts = msg["ts"]
    thread_ts = msg.get("thread_ts")
    is_reply = bool(thread_ts and thread_ts != ts)

    reactions = msg.get("reactions") or []
    reactions_count = sum(r.get("count", 0) for r in reactions)

    ts_url = ts.replace(".", "")
    url = f"https://{workspace}.slack.com/archives/{channel_id}/p{ts_url}"
    ts_iso = datetime.fromtimestamp(float(ts), tz=UTC).isoformat()

    return {
        "key": f"{channel_id}/{ts}",
        "channel_id": channel_id,
        "channel_name": channel_name,
        "author_id": user_id,
        "author_name": author_name,
        "author_email": author_email,
        "summary": clean[:200],
        "text": f"[#{channel_name}] {author_name}: {clean}",
        "ts": ts,
        "thread_ts": thread_ts,
        "is_thread_reply": is_reply,
        "reply_count": msg.get("reply_count") or 0,
        "reactions_count": reactions_count,
        "jira_keys": extract_jira_keys(clean),
        "created": ts_iso,
        "updated": ts_iso,
        "url": url,
        "source": "slack",
        "embedding": None,
    }


def _ingest_users(users_data: list[dict]) -> int:
    coll = slack_users_collection()
    now = datetime.now(UTC)
    ops: list[UpdateOne] = []
    for u in users_data:
        uid = u.get("id")
        if not uid:
            continue
        profile = u.get("profile") or {}
        email = (profile.get("email") or "").lower() or None
        doc = {
            "user_id": uid,
            "name": u.get("name"),
            "display_name": profile.get("display_name")
            or profile.get("real_name")
            or u.get("name"),
            "real_name": profile.get("real_name"),
            "email": email,
            "is_bot": bool(u.get("is_bot")),
            "_synced_at": now,
        }
        ops.append(UpdateOne({"user_id": uid}, {"$set": doc}, upsert=True))
    if ops:
        coll.bulk_write(ops, ordered=False)
    return len(ops)


def _read_json(path_or_zip: Path | zipfile.ZipFile, rel: str) -> Any:
    """Read a JSON file from either a directory path or an open ZipFile."""
    if isinstance(path_or_zip, zipfile.ZipFile):
        try:
            with path_or_zip.open(rel) as f:
                return json.load(f)
        except KeyError:
            return None
    p = path_or_zip / rel
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _iter_channel_files(root: Path | zipfile.ZipFile, channel_name: str) -> list[str]:
    """Return relative paths of all daily JSON files for a channel."""
    if isinstance(root, zipfile.ZipFile):
        prefix = f"{channel_name}/"
        return sorted(
            n
            for n in root.namelist()
            if n.startswith(prefix) and n.endswith(".json") and n != prefix
        )
    channel_dir = root / channel_name
    if not channel_dir.is_dir():
        return []
    return sorted(str(f.relative_to(root)) for f in channel_dir.glob("*.json"))


def main(export_path: str, workspace: str, tenant: str | None = None) -> None:
    from agent_yoku.storage import tenancy

    if tenant:
        tenancy.set_tenant(tenant)

    path = Path(export_path).expanduser().resolve()
    log.info("slack export ingest start path=%s workspace=%s", path, workspace)
    t0 = time.monotonic()

    # Open as zip or directory
    source: Path | zipfile.ZipFile = zipfile.ZipFile(path, "r") if path.suffix == ".zip" else path

    try:
        users_data: list[dict] = _read_json(source, "users.json") or []
        channels_data: list[dict] = _read_json(source, "channels.json") or []
    except Exception as e:
        log.error("failed to read export metadata: %s", e)
        raise

    user_map = _build_user_map(users_data)
    channel_map = _build_channel_map(channels_data)

    n_users = _ingest_users(users_data)
    log.info("upserted %d slack users", n_users)
    log.info("found %d channels in export", len(channel_map))

    coll = slack_messages_collection()
    now = datetime.now(UTC)
    total = 0
    new_or_changed = 0
    batch: list[UpdateOne] = []

    for channel_name, channel_id in sorted(channel_map.items()):
        daily_files = _iter_channel_files(source, channel_name)
        if not daily_files:
            continue

        per_channel = 0
        for rel_path in daily_files:
            messages: list[dict] = _read_json(source, rel_path) or []
            for msg in messages:
                doc = _message_to_doc(msg, channel_id, channel_name, user_map, workspace)
                if doc is None:
                    continue

                key = doc["key"]
                existing = coll.find_one({"key": key}, {"text": 1, "embedding": 1})
                if existing and existing.get("text") == doc["text"]:
                    doc["embedding"] = existing.get("embedding")
                else:
                    new_or_changed += 1

                doc["_synced_at"] = now
                batch.append(UpdateOne({"key": key}, {"$set": doc}, upsert=True))
                per_channel += 1

                if len(batch) >= INGEST_BATCH_SIZE:
                    coll.bulk_write(batch, ordered=False)
                    total += len(batch)
                    batch = []
                    log.info("flushed total=%d new_or_changed=%d", total, new_or_changed)

        log.info("channel=#%s messages=%d", channel_name, per_channel)

    if batch:
        coll.bulk_write(batch, ordered=False)
        total += len(batch)

    if isinstance(source, zipfile.ZipFile):
        source.close()

    elapsed = time.monotonic() - t0
    log.info(
        "slack export ingest done total=%d new_or_changed=%d users=%d elapsed=%.1fs",
        total,
        new_or_changed,
        n_users,
        elapsed,
    )
    return total
