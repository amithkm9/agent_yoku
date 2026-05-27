"""Reverse-enrich: for each GH PR with jira_keys, push a tiny PR ref onto the
matching JIRA ticket's `linked_prs` array.

Run after ingest_github.py. Idempotent ($addToSet).

Usage:
    python link_prs_to_jira.py            # enrich
    python link_prs_to_jira.py --reset    # clear linked_prs first
"""

from __future__ import annotations

import sys
import time

from pymongo import UpdateOne

from agent_yoku.config import github_prs_collection, tickets_collection
from agent_yoku.constants import LINK_BATCH_SIZE
from agent_yoku.log import get_logger

log = get_logger("link_prs")


def main() -> None:
    reset = "--reset" in sys.argv[1:]
    jira_coll = tickets_collection()
    gh_coll = github_prs_collection()

    if reset:
        n = jira_coll.update_many({}, {"$unset": {"linked_prs": ""}}).modified_count
        log.info("cleared linked_prs on %d jira docs", n)

    t0 = time.monotonic()
    ops: list[UpdateOne] = []
    scanned = 0
    linked_keys: set[str] = set()

    cursor = gh_coll.find(
        {"jira_keys": {"$ne": []}},
        {"key": 1, "jira_keys": 1, "status": 1, "url": 1, "summary": 1, "repo": 1, "merged": 1},
    )
    for pr in cursor:
        scanned += 1
        ref = {
            "key": pr["key"],
            "repo": pr.get("repo"),
            "summary": pr.get("summary"),
            "status": pr.get("status"),
            "url": pr.get("url"),
            "merged": pr.get("merged", False),
        }
        for jk in pr["jira_keys"]:
            linked_keys.add(jk)
            ops.append(
                UpdateOne(
                    {"key": jk},
                    {"$pull": {"linked_prs": {"key": pr["key"]}}},
                )
            )
            ops.append(
                UpdateOne(
                    {"key": jk},
                    {"$addToSet": {"linked_prs": ref}},
                )
            )
        if len(ops) >= LINK_BATCH_SIZE:
            jira_coll.bulk_write(ops, ordered=True)
            ops = []

    if ops:
        jira_coll.bulk_write(ops, ordered=True)

    elapsed = time.monotonic() - t0
    log.info(
        "link done prs_scanned=%d unique_jira_keys=%d elapsed=%.1fs",
        scanned,
        len(linked_keys),
        elapsed,
    )


if __name__ == "__main__":
    main()
