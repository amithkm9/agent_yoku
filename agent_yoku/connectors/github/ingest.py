"""Fetch GitHub PRs from all non-archived org repos into mongo.

Usage:
    python ingest_github.py                     # all non-archived repos, last N days
    python ingest_github.py agent-svc dc-okta-user   # restrict to listed repos
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime

from pymongo import UpdateOne

from agent_yoku.connectors._runtime import current_github_config
from agent_yoku.connectors.github.client import (
    is_bot,
    list_non_archived_repos,
    list_pulls,
    lookback_cutoff,
    pr_to_doc,
)
from agent_yoku.log import get_logger
from agent_yoku.storage.mongo import github_prs_collection

log = get_logger("ingest_github")

BATCH = 100


def _repos_to_scan(filter_names: list[str]) -> list[str]:
    org = current_github_config().org
    if filter_names:
        return [f"{org}/{n}" for n in filter_names]
    return [r["full_name"] for r in list_non_archived_repos()]


def main() -> None:
    filter_names = sys.argv[1:]
    cutoff = lookback_cutoff()
    log.info(
        "starting gh ingest org=%s cutoff=%s filter=%s",
        current_github_config().org,
        cutoff.isoformat(),
        filter_names or "<all non-archived>",
    )
    t0 = time.monotonic()

    coll = github_prs_collection()
    repos = _repos_to_scan(filter_names)
    log.info("scanning %d repos", len(repos))

    now = datetime.now(UTC)
    batch: list[UpdateOne] = []
    total = 0
    new_or_changed = 0
    skipped_bots = 0

    for repo_full_name in repos:
        per_repo = 0
        for pr in list_pulls(repo_full_name, cutoff):
            if is_bot(pr):
                skipped_bots += 1
                continue
            doc = pr_to_doc(pr, repo_full_name)
            key = doc["key"]

            existing = coll.find_one({"key": key}, {"text": 1, "embedding": 1})
            if existing and existing.get("text") == doc["text"]:
                doc["embedding"] = existing.get("embedding")
            else:
                doc["embedding"] = None
                new_or_changed += 1

            doc["_synced_at"] = now
            batch.append(UpdateOne({"key": key}, {"$set": doc}, upsert=True))
            per_repo += 1

            if len(batch) >= BATCH:
                coll.bulk_write(batch, ordered=False)
                total += len(batch)
                batch = []
                log.info("flushed total=%d new_or_changed=%d", total, new_or_changed)

        log.info("repo=%s prs=%d", repo_full_name, per_repo)

    if batch:
        coll.bulk_write(batch, ordered=False)
        total += len(batch)

    elapsed = time.monotonic() - t0
    log.info(
        "gh ingest done total=%d new_or_changed=%d skipped_bots=%d elapsed=%.1fs",
        total,
        new_or_changed,
        skipped_bots,
        elapsed,
    )


if __name__ == "__main__":
    main()
