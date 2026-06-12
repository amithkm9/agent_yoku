"""Targeted single-doc refresh — run after a write-back so the very next
answer reflects the change instead of waiting for the hourly sync.

Reuses the connector's own fetch + doc builders and the same diff-on-upsert
event seam as full ingest, then re-projects the canonical doc and invalidates
the tenant's search index. Embedding is cleared on text change and picked up
by the next embed pass (cosine over a None embedding is simply skipped).

Callers bind connector credentials; `executor._sync_after` handles that.
"""

from __future__ import annotations

from datetime import UTC, datetime

from yoku.logging import get_logger
from yoku.proactive.events import TRACKED_FIELDS, diff_events, write_events
from yoku.utils.keys import PR_KEY_RE

log = get_logger("sync_one")


def _strip(key: str) -> str:
    return key.removeprefix("jira/").removeprefix("github/").strip()


def is_pr_key(key: str) -> bool:
    """True when `key` (with or without canonical prefix) is an org/repo#N key."""
    return bool(PR_KEY_RE.match(_strip(key)))


def sync_one(key: str) -> bool:
    """Refresh one JIRA ticket or GitHub PR by key. Returns True on success.

    Caller binds the matching connector credentials (see executor._sync_after).
    """
    raw = _strip(key)
    if PR_KEY_RE.match(raw):
        return _sync_pr(raw)
    return _sync_ticket(raw)


def _upsert(coll, collection_name: str, doc: dict) -> None:
    now = datetime.now(UTC)
    proj = {"text": 1, "embedding": 1, **dict.fromkeys(TRACKED_FIELDS[collection_name], 1)}
    existing = coll.find_one({"key": doc["key"]}, proj)
    if existing and existing.get("text") == doc["text"]:
        doc["embedding"] = existing.get("embedding")
    else:
        doc["embedding"] = None
    write_events(diff_events(collection_name, doc["key"], existing, doc, now))
    doc["_synced_at"] = now
    coll.update_one({"key": doc["key"]}, {"$set": doc}, upsert=True)


def _reproject(raw_doc: dict, mapper, target_coll) -> None:
    canon = mapper(raw_doc).model_dump()
    canon["embedding"] = raw_doc.get("embedding")
    canon["_unified_at"] = datetime.now(UTC)
    target_coll.update_one({"key": canon["key"]}, {"$set": canon}, upsert=True)


def _invalidate() -> None:
    from yoku.agent.tools import invalidate_index
    from yoku.db.tenancy import current_tenant

    try:
        invalidate_index(current_tenant())
    except Exception:
        log.info("index invalidation skipped (no tenant context)")


def _sync_ticket(key: str) -> bool:
    from yoku.connectors.jira.client import get_issue, issue_to_doc
    from yoku.db.mongo import dc_jira_collection, ds_work_item_collection
    from yoku.mappers import SOURCE_MAPPERS

    issue = get_issue(key)
    doc = issue_to_doc(issue)
    coll = dc_jira_collection()
    # Preserve enrichments full ingest doesn't produce per-doc (reverse links).
    prior = coll.find_one({"key": key}, {"linked_prs": 1}) or {}
    if prior.get("linked_prs") and not doc.get("linked_prs"):
        doc["linked_prs"] = prior["linked_prs"]
    _upsert(coll, "dc-jira", doc)
    stored = coll.find_one({"key": key})
    if stored is None:  # vanished between upsert and read — nothing to project
        return False
    _reproject(stored, SOURCE_MAPPERS["dc-jira"], ds_work_item_collection())
    _invalidate()
    log.info("sync_one refreshed jira/%s", key)
    return True


def _sync_pr(key: str) -> bool:
    from yoku.connectors.github.client import get_pull, pr_to_doc
    from yoku.db.mongo import dc_github_collection, ds_pull_request_collection
    from yoku.mappers import SOURCE_MAPPERS

    m = PR_KEY_RE.match(key)
    repo, number = m.group(1), int(m.group(2))
    pr = get_pull(repo, number)
    doc = pr_to_doc(pr, repo)
    coll = dc_github_collection()
    prior = coll.find_one({"key": key}, {"jira_keys": 1}) or {}
    # Keep manually linked tickets the source payload doesn't know about.
    for jk in prior.get("jira_keys") or []:
        if jk not in (doc.get("jira_keys") or []):
            doc.setdefault("jira_keys", []).append(jk)
    _upsert(coll, "dc-github", doc)
    stored = coll.find_one({"key": key})
    if stored is None:  # vanished between upsert and read — nothing to project
        return False
    _reproject(stored, SOURCE_MAPPERS["dc-github"], ds_pull_request_collection())
    _invalidate()
    log.info("sync_one refreshed github/%s", key)
    return True
