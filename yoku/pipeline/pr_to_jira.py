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

from yoku.constants import LINK_BATCH_SIZE
from yoku.db.mongo import dc_github_collection, dc_jira_collection
from yoku.logging import get_logger
from yoku.proactive.events import linked_event, write_events

log = get_logger("link_prs")


def _existing_links() -> dict[str, set[str]]:
    """{jira key -> PR keys already on its linked_prs}, for EVERY ticket — so
    only genuinely new links on tickets that exist emit a `linked` event (the
    pass itself re-upserts every pair, and a dangling ticket ref must not
    re-fire each sync).
    """
    return {
        d["key"]: {p.get("key") for p in d.get("linked_prs") or [] if isinstance(p, dict)}
        for d in dc_jira_collection().find({}, {"key": 1, "linked_prs.key": 1})
    }


def main() -> None:
    reset = "--reset" in sys.argv[1:]
    jira_coll = dc_jira_collection()
    gh_coll = dc_github_collection()

    if reset:
        n = jira_coll.update_many({}, {"$unset": {"linked_prs": ""}}).modified_count
        log.info("cleared linked_prs on %d jira docs", n)

    t0 = time.monotonic()
    ops: list[UpdateOne] = []
    scanned = 0
    linked_keys: set[str] = set()
    prior = _existing_links()
    events: list[dict] = []

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
            if jk in prior and pr["key"] not in prior[jk]:
                events.append(linked_event(jk, pr["key"]))
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
    n_events = write_events(events)

    elapsed = time.monotonic() - t0
    log.info(
        "link done prs_scanned=%d unique_jira_keys=%d new_links=%d elapsed=%.1fs",
        scanned,
        len(linked_keys),
        n_events,
        elapsed,
    )


if __name__ == "__main__":
    main()
