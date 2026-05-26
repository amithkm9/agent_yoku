"""Fetch all tickets from JIRA project and upsert into mongo.

Usage:
    python ingest.py                # all AS tickets, ordered by updated
    python ingest.py 'updated >= -7d'  # custom JQL fragment appended
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime

from pymongo import UpdateOne

from agent_yoku.config import JIRA_PROJECT, tickets_collection
from agent_yoku.connectors.jira.client import issue_to_doc, search_issues
from agent_yoku.log import get_logger

log = get_logger("ingest")


def build_jql(extra: str | None) -> str:
    base = f'project = "{JIRA_PROJECT}"'
    if extra:
        base = f"{base} AND ({extra})"
    return f"{base} ORDER BY updated DESC"


def main() -> None:
    extra = sys.argv[1] if len(sys.argv) > 1 else None
    jql = build_jql(extra)
    log.info("starting ingest jql=%s", jql)
    t0 = time.monotonic()

    coll = tickets_collection()
    batch: list[UpdateOne] = []
    seen_keys: set[str] = set()
    count = 0
    new_or_changed = 0

    now = datetime.now(UTC)
    for issue in search_issues(jql):
        doc = issue_to_doc(issue)
        key = doc["key"]
        if key in seen_keys:
            continue
        seen_keys.add(key)

        existing = coll.find_one({"key": key}, {"text": 1, "embedding": 1})
        if existing and existing.get("text") == doc["text"]:
            doc["embedding"] = existing.get("embedding")
        else:
            doc["embedding"] = None
            new_or_changed += 1

        doc["_synced_at"] = now
        batch.append(UpdateOne({"key": key}, {"$set": doc}, upsert=True))
        if len(batch) >= 100:
            coll.bulk_write(batch, ordered=False)
            count += len(batch)
            log.info("upserted=%d new_or_changed=%d", count, new_or_changed)
            batch = []

    if batch:
        coll.bulk_write(batch, ordered=False)
        count += len(batch)

    elapsed = time.monotonic() - t0
    log.info("ingest done total=%d new_or_changed=%d elapsed=%.1fs", count, new_or_changed, elapsed)


if __name__ == "__main__":
    main()
