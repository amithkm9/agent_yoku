"""Pull org members + their public profiles into mongo.

For each member we capture: login, id, name, email (if public), type (User|Bot),
upsert by login. Run once, then occasionally re-run to refresh joiners/leavers.

Usage:
    python ingest_github_users.py
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import requests
from pymongo import UpdateOne

from yoku.connectors._runtime import current_github_config
from yoku.connectors.github.client import BOT_AUTHORS, _get, _paginate
from yoku.log import get_logger
from yoku.storage.mongo import github_users_collection

log = get_logger("ingest_gh_users")


def _user_profile(login: str) -> dict:
    """Hit /users/{login} for the public profile, including email if exposed."""
    url = f"{current_github_config().api_base_clean}/users/{login}"
    return _get(url).json()


def main() -> None:
    cfg = current_github_config()
    log.info("listing members of %s", cfg.org)
    members_url = f"{cfg.api_base_clean}/orgs/{cfg.org}/members"
    members = list(_paginate(members_url, {"per_page": 100, "filter": "all"}))
    log.info("found %d members", len(members))

    coll = github_users_collection()
    now = datetime.now(UTC)
    t0 = time.monotonic()

    ops = []
    with_email = 0
    for i, m in enumerate(members, 1):
        login = m["login"]
        try:
            profile = _user_profile(login)
        except requests.HTTPError as e:
            log.warning("profile fetch failed for %s: %s", login, e)
            profile = {}

        email = profile.get("email")
        if email:
            with_email += 1

        doc = {
            "login": login,
            "id": m.get("id"),
            "name": profile.get("name"),
            "email": email.lower() if email else None,
            "type": m.get("type") or profile.get("type") or "User",
            "is_bot": (m.get("type") == "Bot") or login.endswith("[bot]") or login in BOT_AUTHORS,
            "company": profile.get("company"),
            "blog": profile.get("blog"),
            "avatar_url": m.get("avatar_url"),
            "html_url": m.get("html_url"),
            "_synced_at": now,
        }
        ops.append(UpdateOne({"login": login}, {"$set": doc}, upsert=True))

        if i % 25 == 0:
            log.info("progress %d/%d with_email=%d", i, len(members), with_email)

    if ops:
        coll.bulk_write(ops, ordered=False)

    elapsed = time.monotonic() - t0
    log.info(
        "gh users done total=%d with_email=%d elapsed=%.1fs", len(members), with_email, elapsed
    )


if __name__ == "__main__":
    main()
