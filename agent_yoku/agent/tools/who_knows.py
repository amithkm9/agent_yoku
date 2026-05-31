"""`who_knows` — people behind a topic, merged across JIRA + GitHub identities."""

from __future__ import annotations

import time

from langchain_core.tools import tool

from agent_yoku.agent import tools as _t
from agent_yoku.agent.tools._ranking import _WHO_KNOWS_RECENCY_FLOOR, _recency_factor, _to_epoch


def _identity_maps() -> tuple[dict[str, dict], dict[str, dict]]:
    """Lookup tables {jira displayName -> unified user} and {gh login -> unified}."""
    by_jira: dict[str, dict] = {}
    by_gh: dict[str, dict] = {}
    for u in _t.unified_users_collection().find({}, {"_id": 0, "embedding": 0}):
        jname = (u.get("jira") or {}).get("displayName")
        glogin = (u.get("github") or {}).get("login")
        if jname:
            by_jira[jname] = u
        if glogin:
            by_gh[glogin] = u
    return by_jira, by_gh


@tool
def who_knows(topic: str, limit: int = 5) -> list[dict]:
    """Find who works on a topic — the people behind the most relevant tickets
    and PRs, merged across their JIRA + GitHub identities.

    Runs a hybrid search for `topic`, tallies JIRA assignees and PR authors over
    the matches, and joins the two identities via unified_users so one person
    counts once. Ranking is recency-weighted: recent work counts toward current
    ownership, while years-old items are discounted (so a long-departed owner
    doesn't top the list). Use for "who's the expert on X?" / "who owns Y now?".

    Returns people ranked by `score` (recency-weighted), with raw counts:
    [{name, email, jira_name, github_login, jira_items, github_items, total, score, sample_keys}].
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic must not be empty")
    limit = max(1, min(int(limit), 25))

    cards = _t.semantic_search.invoke({"query": topic, "k": 40, "source": "both"})
    jira_card_keys = [c["key"] for c in cards if c["source"] == "jira" and c.get("assignee")]
    gh_keys = [c["key"] for c in cards if c["source"] == "github"]

    jira_ts: dict[str, float | None] = {}
    if jira_card_keys:
        jira_ts = {
            d["key"]: _to_epoch(d.get("updated"))
            for d in _t.tickets_collection().find(
                {"key": {"$in": jira_card_keys}}, {"_id": 0, "key": 1, "updated": 1}
            )
        }
    contributions: list[tuple[str, str, str, float | None]] = [
        ("jira", c["assignee"], c["key"], jira_ts.get(c["key"]))
        for c in cards
        if c["source"] == "jira" and c.get("assignee")
    ]
    if gh_keys:
        for d in _t.github_prs_collection().find(
            {"key": {"$in": gh_keys}}, {"_id": 0, "key": 1, "author": 1, "updated": 1}
        ):
            if d.get("author"):
                contributions.append(("github", d["author"], d["key"], _to_epoch(d.get("updated"))))

    by_jira, by_gh = _identity_maps()
    now = time.time()
    tallies: dict[str, dict] = {}
    for kind, raw, key, ts in contributions:
        unified = (by_jira if kind == "jira" else by_gh).get(raw)
        if unified and unified.get("is_bot"):
            continue
        if unified:
            ident = unified.get("user_id") or unified.get("email") or raw
            jira_name = (unified.get("jira") or {}).get("displayName")
            gh_login = (unified.get("github") or {}).get("login")
            email = unified.get("email")
            name = jira_name or gh_login or raw
        else:
            ident = f"{kind}:{raw}"
            jira_name = raw if kind == "jira" else None
            gh_login = raw if kind == "github" else None
            email = None
            name = raw
        t = tallies.setdefault(
            ident,
            {
                "name": name,
                "email": email,
                "jira_name": jira_name,
                "github_login": gh_login,
                "jira_items": 0,
                "github_items": 0,
                "score": 0.0,
                "sample_keys": [],
            },
        )
        t[f"{kind}_items"] += 1
        t["score"] += _WHO_KNOWS_RECENCY_FLOOR + (1 - _WHO_KNOWS_RECENCY_FLOOR) * _recency_factor(
            ts, now
        )
        if len(t["sample_keys"]) < 6:
            t["sample_keys"].append(key)

    people = [
        {**t, "score": round(t["score"], 2), "total": t["jira_items"] + t["github_items"]}
        for t in tallies.values()
    ]
    people.sort(key=lambda p: -p["score"])
    return people[:limit]
