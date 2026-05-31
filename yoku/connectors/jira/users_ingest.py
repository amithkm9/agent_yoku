"""Fetch JIRA users and upsert into mongo `users` collection.

Usage:
    python ingest_users.py
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pymongo import UpdateOne

from yoku.connectors.jira.users_client import search_users, user_to_doc
from yoku.log import get_logger
from yoku.storage.mongo import users_collection

log = get_logger("ingest_users")


def main() -> None:
    log.info("starting users ingest")
    t0 = time.monotonic()
    coll = users_collection()
    now = datetime.now(UTC)
    batch: list[UpdateOne] = []
    seen: set[str] = set()
    count = 0

    for raw in search_users():
        doc = user_to_doc(raw)
        acct = doc.get("accountId")
        if not acct or acct in seen:
            continue
        seen.add(acct)
        doc["_synced_at"] = now
        batch.append(UpdateOne({"accountId": acct}, {"$set": doc}, upsert=True))
        if len(batch) >= 200:
            coll.bulk_write(batch, ordered=False)
            count += len(batch)
            log.info("upserted=%d", count)
            batch = []

    if batch:
        coll.bulk_write(batch, ordered=False)
        count += len(batch)

    log.info("users ingest done total=%d elapsed=%.1fs", count, time.monotonic() - t0)


if __name__ == "__main__":
    main()
