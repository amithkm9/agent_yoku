"""Mongo collection accessors + the agent's read whitelist.

Tenant-aware: each accessor reads `tenancy.current_tenant()` and routes to
`yoku_<tenant>` (or back to `yoku` for the legacy tenant_id).
One mongo cluster, one db per tenant — no cross-tenant queries.

Indexes are ensured once per (db, collection) per process via `_ensure`, not on
every accessor call.

Naming convention:
  dc-*  raw connector collections (internal, not exposed to the agent)
  ds-*  canonical / derived collections (exposed via ALLOWED_COLLECTIONS)
"""

from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from yoku.config import settings
from yoku.db.tenancy import tenant_db_name

# `<db>.<collection>` markers whose indexes have been ensured this process.
_INDEXED: set[str] = set()


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)


def _db() -> Database:
    return _client()[tenant_db_name()]


def _ensure(coll: Collection, specs: list[IndexModel]) -> Collection:
    """Create the collection's indexes once per process, then return it."""
    marker = f"{coll.database.name}.{coll.name}"
    if marker not in _INDEXED:
        coll.create_indexes(specs)
        _INDEXED.add(marker)
    return coll


# ---------------------------------------------------------------------------
# dc-* raw connector collections (internal — not exposed to the agent)
# ---------------------------------------------------------------------------


def dc_jira_collection() -> Collection:
    return _ensure(
        _db()["dc-jira"],
        [
            IndexModel("key", unique=True),
            IndexModel("status"),
            IndexModel("assignee"),
            IndexModel([("updated", DESCENDING)]),
        ],
    )


def dc_jira_users_collection() -> Collection:
    return _ensure(_db()["dc-jira-users"], [IndexModel("accountId", unique=True)])


def dc_github_collection() -> Collection:
    return _ensure(
        _db()["dc-github"],
        [
            IndexModel("key", unique=True),
            IndexModel("jira_keys"),
            IndexModel([("repo", ASCENDING), ("updated", DESCENDING)]),
            IndexModel("status"),
            IndexModel("author"),
        ],
    )


def dc_github_users_collection() -> Collection:
    return _ensure(
        _db()["dc-github-users"],
        [IndexModel("login", unique=True), IndexModel("email")],
    )


def dc_slack_collection() -> Collection:
    return _ensure(
        _db()["dc-slack"],
        [
            IndexModel("key", unique=True),
            IndexModel("channel_name"),
            IndexModel("author_name"),
            IndexModel("jira_keys"),
            IndexModel([("updated", DESCENDING)]),
        ],
    )


def dc_slack_users_collection() -> Collection:
    return _ensure(
        _db()["dc-slack-users"],
        [IndexModel("user_id", unique=True), IndexModel("email", sparse=True)],
    )


# ---------------------------------------------------------------------------
# ds-* canonical / derived collections (exposed to the agent)
# ---------------------------------------------------------------------------

_UNIFIED_DOC_INDEXES = [
    IndexModel("key", unique=True),
    IndexModel("domain"),
    IndexModel("provider"),
    IndexModel("refs"),
    IndexModel([("updated", DESCENDING)]),
]


def ds_work_item_collection() -> Collection:
    """Canonical work-item documents (domain=work_item); populated by `pipeline.unify`."""
    return _ensure(_db()["ds-work-item"], _UNIFIED_DOC_INDEXES)


def ds_pull_request_collection() -> Collection:
    """Canonical pull-request documents (domain=pull_request); populated by `pipeline.unify`."""
    return _ensure(_db()["ds-pull-request"], _UNIFIED_DOC_INDEXES)


def ds_conversation_collection() -> Collection:
    """Canonical conversation documents (domain=conversation); populated by `pipeline.unify`."""
    return _ensure(_db()["ds-conversation"], _UNIFIED_DOC_INDEXES)


def ds_entity_links_collection() -> Collection:
    """Typed cross-source edges (Way B); derived from canonical doc `refs`.

    One row per (from_key, to_key) edge, deduped by that pair so re-runs are
    idempotent. Populated by `pipeline.entity_links`.
    """
    return _ensure(
        _db()["ds-entity-links"],
        [
            IndexModel([("from_key", ASCENDING), ("to_key", ASCENDING)], unique=True),
            IndexModel("from_key"),
            IndexModel("to_key"),
            IndexModel("link_type"),
        ],
    )


def ds_unified_users_collection() -> Collection:
    return _ensure(
        _db()["ds-unified-users"],
        [
            IndexModel("user_id", unique=True),
            IndexModel("email"),
            IndexModel("jira.accountId", sparse=True),
            IndexModel("github.login", sparse=True),
            IndexModel("jira.displayName", sparse=True),
            IndexModel("slack.user_id", sparse=True),
        ],
    )


# ---------------------------------------------------------------------------
# Non-data collections (sessions, auth — not renamed)
# ---------------------------------------------------------------------------


def chat_sessions_collection() -> Collection:
    return _ensure(
        _db()["chat_sessions"],
        [
            IndexModel("session_id", unique=True),
            IndexModel([("last_active_at", DESCENDING)]),
        ],
    )


def chat_messages_collection() -> Collection:
    return _ensure(
        _db()["chat_messages"],
        [
            IndexModel(
                [("session_id", ASCENDING), ("turn_seq", ASCENDING), ("msg_idx", ASCENDING)]
            ),
            IndexModel("turn_id"),
        ],
    )


def auth_users_collection() -> Collection:
    """Login users (email + bcrypt password). Per-tenant, isolated."""
    return _ensure(_db()["auth_users"], [IndexModel("email", unique=True)])


def events_collection() -> Collection:
    """Proactive-engine event stream (docs/yoku_agent.md Phase 1).

    Engine-internal: deliberately NOT in ALLOWED_COLLECTIONS — the agent never
    reads it. Written by connector ingests (diff-on-upsert) and pr_to_jira;
    consumed by detectors and trend analytics.
    """
    return _ensure(
        _db()["events"],
        [
            IndexModel("doc_key"),
            IndexModel([("ts", DESCENDING)]),
            IndexModel([("processed", ASCENDING), ("ts", ASCENDING)]),
            IndexModel("kind"),
        ],
    )


def signals_collection() -> Collection:
    """Candidate gaps emitted by proactive detectors (docs/yoku_agent.md Phase 2).

    Engine-internal like `events`. One row per (detector, item_key) — re-detection
    refreshes the row, never duplicates it.
    """
    return _ensure(
        _db()["signals"],
        [
            IndexModel([("detector", ASCENDING), ("item_key", ASCENDING)], unique=True),
            IndexModel("signal_id", unique=True),
            IndexModel([("status", ASCENDING), ("matured_at", DESCENDING)]),
        ],
    )


# ---------------------------------------------------------------------------
# Internal dc-* accessor map — for pipeline/embed code that reads raw collections
DC_COLLECTIONS = {
    "dc-jira": dc_jira_collection,
    "dc-github": dc_github_collection,
    "dc-slack": dc_slack_collection,
    "dc-jira-users": dc_jira_users_collection,
    "dc-github-users": dc_github_users_collection,
    "dc-slack-users": dc_slack_users_collection,
}

# Agent read whitelist — only ds-* collections
# ---------------------------------------------------------------------------

ALLOWED_COLLECTIONS = {
    "ds-work-item": ds_work_item_collection,
    "ds-pull-request": ds_pull_request_collection,
    "ds-conversation": ds_conversation_collection,
    "ds-entity-links": ds_entity_links_collection,
    "ds-unified-users": ds_unified_users_collection,
}


def get_collection(name: str) -> Collection:
    if name not in ALLOWED_COLLECTIONS:
        raise ValueError(f"Unknown collection {name!r}. Allowed: {sorted(ALLOWED_COLLECTIONS)}")
    return ALLOWED_COLLECTIONS[name]()


def ping() -> None:
    _client().admin.command("ping")
