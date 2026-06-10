"""Build unified_users by joining JIRA + GitHub + Slack users on lowercased email.

Idempotent: re-running rebuilds the collection from scratch (drop + rebuild).
Records:
  user_id        synthetic UUID (stable across rebuilds — keyed off email or
                 (jira_account_id, github_login) for emailless rows)
  email          canonical join key (lowercase) or null
  jira           {accountId, displayName, emailAddress, active}
  github         {login, id, name, email, is_bot}
  slack          {user_id, name, display_name, real_name, email, is_bot}
  is_bot         true if either side is a bot
  match_source   "email" | "name" | "manual" | "jira_only" | "github_only" | "slack_only"

Slack joins onto the already-merged JIRA↔GitHub rows (email exact, then
name-slug fallback — the same strategy as the primary join). Slack emails also
backfill `email` on rows that matched by name but had none, since JIRA/GitHub
often withhold emails while Slack exposes them.

Usage:
    yoku unify-users
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from datetime import UTC, datetime

from yoku.db.mongo import (
    dc_github_users_collection,
    dc_jira_users_collection,
    dc_slack_users_collection,
    ds_unified_users_collection,
)
from yoku.logging import get_logger

log = get_logger("build_users")

# Manual aliases for users whose JIRA displayName and GH login don't
# fuzzy-match (e.g. nicknames, handles unrelated to real name).
# Add new entries here when an active engineer ends up in github_only.
MANUAL_ALIASES: dict[str, str] = {
    "internet-zero": "akshay.reddy@asato.ai",
    "brysonasato": "bryson.chen@asato.ai",
    "kcsraju": "krishna.sagiraju@asato.ai",
    "Kisan-rai": "kisan.rai@asato.ai",
    "mgowri05": "mangala.gowri.mirji@asato.ai",
    "nagavemz": "nagasudeep.vemula@asato.ai",
    "Nanda-Asato": "nanda.vijaydev@asato.ai",
    "parikshit-maheshwari-asato": "parikshit.maheshwari@asato.ai",
    "rajatbindal0406": "rajat.bindal@asato.ai",
    "rkishore-asato": "ramaswami.kishore@asato.ai",
    "RNithya-29": "r.nithya@asato.ai",
    "vikasbg007": "vikas.bg@asato.ai",
    "abhiinasato": "abhinandan.narendra@asato.ai",
    "anush-asato": "anush.mohandass@asato.ai",
}


def _slug(s: str) -> str:
    """Normalize 'Vikas Gangadhar' -> 'vikasgangadhar' for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _stable_user_id(*parts: str | None) -> str:
    h = hashlib.sha256("|".join(p or "" for p in parts).encode()).hexdigest()
    return h[:24]


def _slack_block(s: dict) -> dict:
    return {
        "user_id": s["user_id"],
        "name": s.get("name"),
        "display_name": s.get("display_name"),
        "real_name": s.get("real_name"),
        "email": (s.get("email") or "").lower() or None,
        "is_bot": bool(s.get("is_bot")),
    }


def _fold_in_slack_users(unified: list[dict]) -> dict[str, int]:
    """Attach a `slack` block to existing rows (email exact → name slug) and
    append `slack_only` rows for people who exist only in Slack. Mutates
    `unified` in place; returns match counters for logging.
    """
    slack_users = list(
        dc_slack_users_collection().find(
            {},
            {
                "_id": 0,
                "user_id": 1,
                "name": 1,
                "display_name": 1,
                "real_name": 1,
                "email": 1,
                "is_bot": 1,
            },
        )
    )

    by_email: dict[str, dict] = {}
    by_slug: dict[str, dict] = {}
    for u in unified:
        u.setdefault("slack", None)
        if u.get("email"):
            by_email.setdefault(u["email"], u)
        j_name = (u.get("jira") or {}).get("displayName")
        gh = u.get("github") or {}
        for candidate in (j_name, gh.get("name"), gh.get("login")):
            s = _slug(candidate)
            if s:
                by_slug.setdefault(s, u)

    matched_email = 0
    matched_name = 0
    slack_only = 0
    for s in slack_users:
        s_email = (s.get("email") or "").lower() or None
        target = by_email.get(s_email) if s_email else None
        if target is not None:
            matched_email += 1
        else:
            for candidate in (s.get("real_name"), s.get("display_name"), s.get("name")):
                slug = _slug(candidate)
                if slug and slug in by_slug:
                    target = by_slug[slug]
                    matched_name += 1
                    break

        if target is not None:
            if target.get("slack") is None:
                target["slack"] = _slack_block(s)
            # Slack often exposes emails JIRA/GitHub withhold — backfill.
            if not target.get("email") and s_email:
                target["email"] = s_email
                by_email.setdefault(s_email, target)
            continue

        unified.append(
            {
                "user_id": _stable_user_id(s_email, "slack", s["user_id"]),
                "email": s_email,
                "jira": None,
                "github": None,
                "slack": _slack_block(s),
                "is_bot": bool(s.get("is_bot")),
                "match_source": "slack_only",
            }
        )
        slack_only += 1

    return {
        "inputs": len(slack_users),
        "email": matched_email,
        "name": matched_name,
        "slack_only": slack_only,
    }


def main() -> None:
    log.info("building unified_users…")
    t0 = time.monotonic()

    jira_users = list(
        dc_jira_users_collection().find(
            {},
            {"_id": 0, "accountId": 1, "displayName": 1, "emailAddress": 1, "active": 1},
        )
    )
    gh_users = list(
        dc_github_users_collection().find(
            {},
            {"_id": 0, "login": 1, "id": 1, "name": 1, "email": 1, "is_bot": 1},
        )
    )
    log.info("inputs jira=%d github=%d", len(jira_users), len(gh_users))

    # Index by lowercased email + by slug-of-name for fuzzy fallback.
    gh_by_email: dict[str, dict] = {}
    gh_by_slug: dict[str, dict] = {}
    gh_remaining: dict[str, dict] = {u["login"]: u for u in gh_users}
    for u in gh_users:
        if u.get("email"):
            gh_by_email[u["email"].lower()] = u
        if u.get("name"):
            gh_by_slug.setdefault(_slug(u["name"]), u)
        gh_by_slug.setdefault(_slug(u["login"]), u)

    unified: list[dict] = []
    matched_via_email = 0
    matched_via_name = 0
    matched_manual = 0

    for j in jira_users:
        j_email = (j.get("emailAddress") or "").lower() or None
        j_slug = _slug(j.get("displayName"))
        gh = None
        match_source = "jira_only"

        if j_email and j_email in gh_by_email:
            gh = gh_by_email[j_email]
            match_source = "email"
            matched_via_email += 1
        elif j_slug and j_slug in gh_by_slug:
            gh = gh_by_slug[j_slug]
            match_source = "name"
            matched_via_name += 1

        if gh:
            gh_remaining.pop(gh["login"], None)

        email = j_email or (gh.get("email") if gh else None)
        unified.append(
            {
                "user_id": _stable_user_id(
                    email, j.get("accountId"), gh.get("login") if gh else None
                ),
                "email": email,
                "jira": {
                    "accountId": j.get("accountId"),
                    "displayName": j.get("displayName"),
                    "emailAddress": j_email,
                    "active": j.get("active", True),
                },
                "github": (
                    {
                        "login": gh["login"],
                        "id": gh.get("id"),
                        "name": gh.get("name"),
                        "email": gh.get("email"),
                        "is_bot": bool(gh.get("is_bot")),
                    }
                    if gh
                    else None
                ),
                "is_bot": bool(gh and gh.get("is_bot")),
                "match_source": match_source,
            }
        )

    # Manual alias overrides — pull a remaining GH user onto an existing record.
    for gh_login, jira_email in MANUAL_ALIASES.items():
        gh = gh_remaining.pop(gh_login, None)
        if not gh:
            continue
        target = next((u for u in unified if u["jira"]["emailAddress"] == jira_email.lower()), None)
        if not target:
            continue
        target["github"] = {
            "login": gh["login"],
            "id": gh.get("id"),
            "name": gh.get("name"),
            "email": gh.get("email"),
            "is_bot": bool(gh.get("is_bot")),
        }
        target["match_source"] = "manual"
        target["is_bot"] = target["is_bot"] or bool(gh.get("is_bot"))
        target["user_id"] = _stable_user_id(
            target.get("email"), target["jira"]["accountId"], gh_login
        )
        matched_manual += 1

    # GH users with no JIRA twin become github_only rows.
    for gh in gh_remaining.values():
        unified.append(
            {
                "user_id": _stable_user_id(gh.get("email"), None, gh["login"]),
                "email": gh.get("email"),
                "jira": None,
                "github": {
                    "login": gh["login"],
                    "id": gh.get("id"),
                    "name": gh.get("name"),
                    "email": gh.get("email"),
                    "is_bot": bool(gh.get("is_bot")),
                },
                "is_bot": bool(gh.get("is_bot")),
                "match_source": "github_only",
            }
        )

    slack_stats = _fold_in_slack_users(unified)

    now = datetime.now(UTC)
    for u in unified:
        u["_synced_at"] = now

    coll = ds_unified_users_collection()
    coll.delete_many({})
    if unified:
        coll.insert_many(unified, ordered=False)

    by_source = Counter(u["match_source"] for u in unified)

    elapsed = time.monotonic() - t0
    log.info(
        "unified_users built total=%d email=%d name=%d manual=%d "
        "jira_only=%d github_only=%d slack_email=%d slack_name=%d "
        "slack_only=%d elapsed=%.1fs",
        len(unified),
        matched_via_email,
        matched_via_name,
        matched_manual,
        by_source.get("jira_only", 0),
        by_source.get("github_only", 0),
        slack_stats["email"],
        slack_stats["name"],
        by_source.get("slack_only", 0),
        elapsed,
    )


if __name__ == "__main__":
    main()
