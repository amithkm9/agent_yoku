"""Backfill `author_email` and `assignee_email` on github_prs from unified_users.

Reads the github.login -> email map from unified_users, then in one pass updates
every PR doc with the resolved email. Cheap, idempotent.

Usage:
    python backfill_pr_author_email.py
"""

from __future__ import annotations

import time

from pymongo import UpdateMany

from yoku.core.config import github_prs_collection, unified_users_collection
from yoku.core.logging import get_logger

log = get_logger("backfill_pr_emails")


def main() -> None:
    t0 = time.monotonic()
    login_to_email: dict[str, str] = {}
    for u in unified_users_collection().find(
        {"github.login": {"$exists": True, "$ne": None}, "email": {"$ne": None}},
        {"github.login": 1, "email": 1},
    ):
        login_to_email[u["github"]["login"]] = u["email"]
    log.info("loaded %d login->email mappings", len(login_to_email))

    coll = github_prs_collection()
    ops = []
    for login, email in login_to_email.items():
        ops.append(UpdateMany({"author": login}, {"$set": {"author_email": email}}))
        ops.append(UpdateMany({"assignee": login}, {"$set": {"assignee_email": email}}))

    result = coll.bulk_write(ops, ordered=False)
    elapsed = time.monotonic() - t0
    log.info(
        "backfill done matched=%d modified=%d elapsed=%.1fs",
        result.matched_count,
        result.modified_count,
        elapsed,
    )


if __name__ == "__main__":
    main()
