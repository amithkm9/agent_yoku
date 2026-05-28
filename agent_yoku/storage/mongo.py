"""Mongo collection accessors + the agent's read whitelist.

Tenant-aware: each accessor reads `tenancy.current_tenant()` and routes to
`agent_yoku_<tenant>` (or back to `agent_yoku` for the legacy tenant_id).
One mongo cluster, one db per tenant — no cross-tenant queries.

Indexes are created lazily on first use; cheap because mongo is idempotent.
"""

from __future__ import annotations

from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from agent_yoku.config import settings
from agent_yoku.storage.tenancy import tenant_db_name


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)


def _db() -> Database:
    return _client()[tenant_db_name()]


def tickets_collection() -> Collection:
    coll = _db()["jira_tickets"]
    coll.create_index("key", unique=True)
    return coll


def users_collection() -> Collection:
    coll = _db()["users"]
    coll.create_index("accountId", unique=True)
    return coll


def github_prs_collection() -> Collection:
    coll = _db()["github_prs"]
    coll.create_index("key", unique=True)
    coll.create_index("jira_keys")
    coll.create_index([("repo", 1), ("updated", -1)])
    return coll


def github_users_collection() -> Collection:
    coll = _db()["github_users"]
    coll.create_index("login", unique=True)
    coll.create_index("email")
    return coll


def unified_users_collection() -> Collection:
    coll = _db()["unified_users"]
    coll.create_index("user_id", unique=True)
    coll.create_index("email")
    coll.create_index("jira.accountId", sparse=True)
    coll.create_index("github.login", sparse=True)
    coll.create_index("jira.displayName", sparse=True)
    return coll


def chat_sessions_collection() -> Collection:
    coll = _db()["chat_sessions"]
    coll.create_index("session_id", unique=True)
    coll.create_index([("last_active_at", -1)])
    return coll


def chat_messages_collection() -> Collection:
    coll = _db()["chat_messages"]
    coll.create_index([("session_id", 1), ("turn_seq", 1), ("msg_idx", 1)])
    coll.create_index("turn_id")
    return coll


def auth_users_collection() -> Collection:
    """Login users (email + bcrypt password). Per-tenant, isolated."""
    coll = _db()["auth_users"]
    coll.create_index("email", unique=True)
    return coll


def slack_messages_collection() -> Collection:
    coll = _db()["slack_messages"]
    coll.create_index("key", unique=True)
    coll.create_index("channel_id")
    coll.create_index([("updated", -1)])
    return coll


def slack_users_collection() -> Collection:
    coll = _db()["slack_users"]
    coll.create_index("user_id", unique=True)
    coll.create_index("email", sparse=True)
    return coll


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
