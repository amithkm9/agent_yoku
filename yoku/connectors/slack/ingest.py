"""Fetch Slack public channel messages and upsert into mongo.

Usage:
    yoku ingest slack          # all public channels, configured lookback
    yoku ingest slack general engineering   # restrict to listed channel names
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pymongo import UpdateOne

from yoku.connectors._runtime import current_slack_config
from yoku.connectors.slack.client import (
    fetch_user_map,
    join_channel,
    list_channels,
    list_messages,
    lookback_oldest,
    message_to_doc,
)
from yoku.constants import INGEST_BATCH_SIZE
from yoku.db.mongo import dc_slack_collection
from yoku.logging import get_logger
from yoku.proactive.events import diff_events, write_events

log = get_logger("ingest_slack")


def main(filter_channels: list[str] | None = None) -> None:
    cfg = current_slack_config()
    oldest = lookback_oldest()
    log.info(
        "slack ingest start workspace=%s lookback_days=%d channel_types=%s filter=%s",
        cfg.workspace,
        cfg.lookback_days,
        cfg.channel_types,
        filter_channels or "<all>",
    )
    t0 = time.monotonic()

    user_map = fetch_user_map()
    log.info("resolved %d workspace users", len(user_map))

    channels = [
        ch
        for ch in list_channels(cfg.channel_types)
        if not filter_channels or ch["name"] in filter_channels
    ]
    log.info("scanning %d channel(s)", len(channels))

    coll = dc_slack_collection()
    now = datetime.now(UTC)
    batch: list[UpdateOne] = []
    total = 0
    new_or_changed = 0
    events: list[dict] = []

    for ch in channels:
        channel_id = ch["id"]
        channel_name = ch["name"]
        per_channel = 0

        joined = join_channel(channel_id)
        if not joined and not ch.get("is_member"):
            log.warning(
                "channel=#%s skipped — bot not a member and channels:join scope missing",
                channel_name,
            )
            continue
        for msg in list_messages(channel_id, oldest):
            doc = message_to_doc(msg, channel_id, channel_name, user_map, cfg.workspace)
            key = doc["key"]

            existing = coll.find_one({"key": key}, {"text": 1, "embedding": 1, "_base_text": 1})
            # Compare against the pre-roll original (`_base_text`) when the
            # thread rollup has rewritten `text` to the whole discussion.
            if existing and (existing.get("_base_text") or existing.get("text")) == doc["text"]:
                doc["embedding"] = existing.get("embedding")
                if existing.get("_base_text"):
                    doc["_base_text"] = existing["_base_text"]
                    doc["text"] = existing["text"]
            else:
                doc["embedding"] = None
                doc["_base_text"] = None  # new/edited — rollup re-derives
                new_or_changed += 1
            events.extend(diff_events("dc-slack", key, existing, doc, now))

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
    write_events(events)

    elapsed = time.monotonic() - t0
    log.info(
        "slack ingest done total=%d new_or_changed=%d elapsed=%.1fs",
        total,
        new_or_changed,
        elapsed,
    )
