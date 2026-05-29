"""Mongo collection accessors + the agent's read whitelist.

Tenant-aware: each accessor reads `tenancy.current_tenant()` and routes to
`agent_yoku_<tenant>` (or back to `agent_yoku` for the legacy tenant_id).
One mongo cluster, one db per tenant — no cross-tenant queries.

Indexes are ensured once per (db, collection) per process via `_ensure`, not on
every accessor call.
"""

from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from agent_yoku.config import settings
from agent_yoku.storage.tenancy import tenant_db_name

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


def tickets_collection() -> Collection:
    return _ensure(
        _db()["jira_tickets"],
        [
            IndexModel("key", unique=True),
            IndexModel("status"),
            IndexModel("assignee"),
            IndexModel([("updated", DESCENDING)]),
        ],
    )


def users_collection() -> Collection:
    return _ensure(_db()["users"], [IndexModel("accountId", unique=True)])


def github_prs_collection() -> Collection:
    return _ensure(
        _db()["github_prs"],
        [
            IndexModel("key", unique=True),
            IndexModel("jira_keys"),
            IndexModel([("repo", ASCENDING), ("updated", DESCENDING)]),
            IndexModel("status"),
            IndexModel("author"),
        ],
    )


def github_users_collection() -> Collection:
    return _ensure(
        _db()["github_users"],
        [IndexModel("login", unique=True), IndexModel("email")],
    )


def unified_users_collection() -> Collection:
    return _ensure(
        _db()["unified_users"],
        [
            IndexModel("user_id", unique=True),
            IndexModel("email"),
            IndexModel("jira.accountId", sparse=True),
            IndexModel("github.login", sparse=True),
            IndexModel("jira.displayName", sparse=True),
        ],
    )


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


def slack_messages_collection() -> Collection:
    return _ensure(
        _db()["slack_messages"],
        [
            IndexModel("key", unique=True),
            IndexModel("channel_id"),
            IndexModel([("updated", DESCENDING)]),
        ],
    )


def slack_users_collection() -> Collection:
    return _ensure(
        _db()["slack_users"],
        [IndexModel("user_id", unique=True), IndexModel("email", sparse=True)],
    )


ALLOWED_COLLECTIONS = {
    "jira_tickets": tickets_collection,
    "users": users_collection,
    "github_prs": github_prs_collection,
    "github_users": github_users_collection,
    "unified_users": unified_users_collection,
    "slack_messages": slack_messages_collection,
    "slack_users": slack_users_collection,
}


def get_collection(name: str) -> Collection:
    if name not in ALLOWED_COLLECTIONS:
        raise ValueError(f"Unknown collection {name!r}. Allowed: {sorted(ALLOWED_COLLECTIONS)}")
    return ALLOWED_COLLECTIONS[name]()


def ping() -> None:
    _client().admin.command("ping")
