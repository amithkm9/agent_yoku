"""Tools the deepagent uses to navigate connector data (JIRA, GitHub, Slack, …).

Design principles:
- Each tool is a thin wrapper around mongo or numpy. No LLM calls inside tools.
- Tools return JSON-serializable data (no ObjectId, no datetime objects).
- Lightweight by default; the agent calls get_* tools to drill into specific items.
- Errors raise ValueError with a clear message so the agent can adapt.
- Schema-driven: collection descriptions + filterable fields come from the
  Pydantic models via `schema_registry`; cross-collection links from
  `relationships`; key routing from `sources`. Onboarding a connector is a model
  + a SourceSpec + relationship entries, not edits across every tool here.

Layout: each LangChain tool lives in its own module; the pure scoring/fusion
helpers live in `_ranking` (imported there directly); the stateful index cache
and the seams tests stub (`_embed_query`, `_load_index`, `_index`, `rerank`,
`get_collection`, the collection accessors, …) live here. `ALL_TOOLS` is
assembled by auto-discovery (`_registry`), so adding a tool file needs no edit
to a central list.

`ALLOWED_COLLECTIONS` and the `*_collection` accessors are re-exported here
(and pinned in `__all__`) so the tool modules can reach them as `_t.<name>` and
tests can stub a single seam; without `__all__`, autoflake would prune the ones
this module doesn't reference in its own body.
"""

from __future__ import annotations

import re
import threading
import time
from functools import lru_cache
from typing import Any

import numpy as np
from langchain_core.tools import BaseTool

from agent_yoku.agent.relationships import outbound_relationships
from agent_yoku.agent.rerank import rerank
from agent_yoku.agent.sources import embeddable_sources, get_source, source_for_key
from agent_yoku.agent.tools._ranking import (
    _RECENCY_HALFLIFE_DAYS,
    _STOP_DF_MIN,
    _STOP_DF_RATIO,
    _W_ADJ,
    _W_RECENCY,
    _adjacency_scores,
    _doc_keyword_text,
    _feature_rerank,
    _lexical_overlap,
    _recency_factor,
    _to_epoch,
    _tokenize,
)
from agent_yoku.config import (
    ALLOWED_COLLECTIONS,
    EMBED_MODEL,
    get_collection,
    github_prs_collection,
    openai_client,
    settings,
    tickets_collection,
    unified_users_collection,
)
from agent_yoku.log import get_logger
from agent_yoku.storage.tenancy import current_tenant
from agent_yoku.utils import bson_safe

__all__ = [
    "ALLOWED_COLLECTIONS",
    "ALL_TOOLS",
    "BaseTool",
    "_RECENCY_HALFLIFE_DAYS",
    "_adjacency_scores",
    "_feature_rerank",
    "_lexical_overlap",
    "_recency_factor",
    "_tokenize",
    "bson_safe",
    "get_all_tools",
    "get_collection",
    "github_prs_collection",
    "invalidate_index",
    "tickets_collection",
    "unified_users_collection",
]

log = get_logger("tools")

# Per-tenant index cache. `_INDEX_LOCK` guards only the dict + lock table (never
# held across a rebuild's Mongo I/O); each tenant rebuilds under its own lock.
_INDEX_LOCK = threading.Lock()
_TENANT_LOCKS: dict[str, threading.Lock] = {}
_INDEXES: dict[str, dict[str, Any]] = {}
_INDEX_TTL_S = 600
_MAX_CACHED_TENANTS = 8


def _index_key() -> str:
    try:
        return current_tenant()
    except RuntimeError:
        return "_default"  # CLI / tests may run without a tenant context set


def _load_index() -> dict[str, Any]:
    """Build the unified JIRA + PR index for the current tenant."""
    log.info("loading unified embedding index…")
    proj = {
        "key": 1,
        "summary": 1,
        "status": 1,
        "assignee": 1,
        "url": 1,
        "embedding": 1,
        "jira_keys": 1,
        "linked_prs": 1,
        "repo": 1,
        "updated": 1,
    }

    docs: list[dict] = []
    counts: dict[str, int] = {}
    for spec in embeddable_sources():
        rows = list(
            get_collection(spec.collection).find(
                {"embedding": {"$ne": None, "$exists": True}}, proj
            )
        )
        for d in rows:
            d["source"] = spec.name
        counts[spec.name] = len(rows)
        docs.extend(rows)
    if not docs:
        raise RuntimeError("no embedded docs found; run ingest + embed first")

    matrix = np.array([d["embedding"] for d in docs], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
    for d in docs:
        d.pop("embedding", None)
        d.pop("_id", None)
        # Keys of the cross-source items this doc links to — a PR's referenced
        # tickets, or the PRs linked onto a ticket. Doubles as the corroboration
        # flag and as the lookup for query-aware adjacency reranking.
        spec = get_source(d["source"])
        outs = outbound_relationships(spec.collection) if spec else []
        if outs:
            d["link_keys"] = list(d.get(outs[0].join.local_field) or [])
        else:
            # The link hub (e.g. JIRA) has no outbound relationship; its links are
            # materialised on the doc as `linked_prs` at ingest time.
            d["link_keys"] = [
                p["key"]
                for p in (d.get("linked_prs") or [])
                if isinstance(p, dict) and p.get("key")
            ]
        d["has_links"] = bool(d["link_keys"])
        d.pop("linked_prs", None)
        d["updated_ts"] = _to_epoch(d.get("updated"))
        d.pop("updated", None)

    inverted: dict[str, list[int]] = {}
    for i, d in enumerate(docs):
        for tok in _tokenize(_doc_keyword_text(d)):
            inverted.setdefault(tok, []).append(i)
    stop_df = max(_STOP_DF_MIN, int(_STOP_DF_RATIO * len(docs)))
    inverted = {tok: post for tok, post in inverted.items() if len(post) <= stop_df}
    idf = {tok: float(np.log(1.0 + len(docs) / len(post))) for tok, post in inverted.items()}

    key_to_idx = {d["key"]: i for i, d in enumerate(docs)}

    log.info("index loaded %s", " ".join(f"{k}={v}" for k, v in counts.items()))
    return {
        "docs": docs,
        "matrix": matrix,
        "inverted": inverted,
        "idf": idf,
        "key_to_idx": key_to_idx,
    }


def _fresh(idx: dict[str, Any]) -> bool:
    return (time.monotonic() - idx["_built_at"]) < _INDEX_TTL_S


def _tenant_lock(key: str) -> threading.Lock:
    """Get (or lazily create) the rebuild lock for a tenant."""
    with _INDEX_LOCK:
        lock = _TENANT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _TENANT_LOCKS[key] = lock
        return lock


def _cache_get(key: str) -> dict[str, Any] | None:
    """Return a cached index, marking it most-recently-used."""
    with _INDEX_LOCK:
        idx = _INDEXES.pop(key, None)
        if idx is not None:
            _INDEXES[key] = idx
        return idx


def _cache_put(key: str, idx: dict[str, Any]) -> None:
    """Store an index, evicting the least-recently-used tenant past the cap."""
    with _INDEX_LOCK:
        _INDEXES[key] = idx
        while len(_INDEXES) > _MAX_CACHED_TENANTS:
            oldest = next(iter(_INDEXES))
            _INDEXES.pop(oldest, None)


def _index() -> dict[str, Any]:
    """Return the current tenant's index, building it on first use and rebuilding
    once it ages past the TTL.

    Rebuilds run under a per-tenant lock so one tenant's reload never blocks
    another's searches, and the cache is LRU-bounded by `_MAX_CACHED_TENANTS`.
    """
    key = _index_key()
    idx = _cache_get(key)
    if idx is not None and _fresh(idx):
        return idx
    with _tenant_lock(key):
        idx = _cache_get(key)  # re-check under the tenant lock
        if idx is None or not _fresh(idx):
            idx = _load_index()
            idx["_built_at"] = time.monotonic()
            _cache_put(key, idx)
        return idx


def invalidate_index(tenant: str | None = None) -> None:
    """Drop a tenant's cached index so the next search rebuilds it — call after a
    sync re-embeds data. Defaults to the current tenant."""
    key = tenant or _index_key()
    with _INDEX_LOCK:
        _INDEXES.pop(key, None)


@lru_cache(maxsize=512)
def _embed_query(text: str) -> np.ndarray:
    resp = openai_client().embeddings.create(model=EMBED_MODEL, input=[text])
    v = np.array(resp.data[0].embedding, dtype=np.float32)
    v /= np.linalg.norm(v) + 1e-12
    v.flags.writeable = False
    return v


def _clean(doc: dict, drop: tuple[str, ...] = ()) -> dict:
    """Strip _id + caller-named fields, then recursively JSON-safe the rest.

    Mongo can return nested ObjectId / datetime values (e.g. JIRA's
    `_synced_at`, the linked_prs blobs). Without bson_safe, those crash the
    LangChain tool serializer mid-loop. Cheap and always-safe to apply.
    """
    out = {k: v for k, v in doc.items() if k not in drop and k != "_id"}
    return bson_safe(out)


def _fetch_texts(keys: list[str]) -> dict[str, str]:
    """Pull the full embedded `text` for the given doc keys, grouped by source."""
    by_collection: dict[str, list[str]] = {}
    for k in keys:
        spec = source_for_key(k)
        if spec:
            by_collection.setdefault(spec.collection, []).append(k)
    out: dict[str, str] = {}
    for collection, group in by_collection.items():
        for d in get_collection(collection).find(
            {"key": {"$in": group}}, {"_id": 0, "key": 1, "text": 1}
        ):
            out[d["key"]] = d.get("text") or ""
    return out


def _rerank_texts(indices: list[int], idx: dict[str, Any]) -> list[str]:
    """Reranker input for each candidate: the full embedded body, summary on miss."""
    docs = idx["docs"]
    by_key = _fetch_texts([docs[i]["key"] for i in indices])
    return [by_key.get(docs[i]["key"]) or (docs[i].get("summary") or "") for i in indices]


def _rerank_order(
    query: str,
    window: list[int],
    fused: dict[int, float],
    idx: dict[str, Any],
    adjacency: dict[int, float],
) -> list[int]:
    """Final ordering for the fused window.

    Reranks the top `rerank_top_n` candidates with ZeroEntropy (reading their full
    body text), then layers the graph signals the reranker can't see — cross-source
    adjacency and recency — onto its relevance score. Any candidates past the cap
    keep their fused order beneath. Falls back to the built-in feature reranker
    whenever ZeroEntropy is disabled or unavailable, so search never depends on it.
    """
    head, tail = window[: settings.rerank_top_n], window[settings.rerank_top_n :]
    scored_ze = rerank(query, _rerank_texts(head, idx))
    if not scored_ze:  # None (disabled/error) or [] (empty ZE response)
        return _feature_rerank(query, [(i, fused[i]) for i in window], idx, adjacency)

    now = time.time()
    docs = idx["docs"]
    seen_local: set[int] = set()
    boosted = []
    for local_i, relevance in scored_ze:
        # Skip negative, out-of-range, or duplicate indices from a malformed ZE response.
        if local_i < 0 or local_i >= len(head) or local_i in seen_local:
            continue
        seen_local.add(local_i)
        i = head[local_i]
        adj = adjacency.get(i, 0.0)
        rec = _recency_factor(docs[i].get("updated_ts"), now)
        boosted.append((i, relevance * (1.0 + _W_ADJ * adj + _W_RECENCY * rec)))
    # Guard: if ZeroEntropy omits any head items (unexpected), append them at the bottom.
    for local_i in range(len(head)):
        if local_i not in seen_local:
            boosted.append((head[local_i], 0.0))
    boosted.sort(key=lambda x: -x[1])
    return [i for i, _ in boosted] + tail


def _resolve_user_doc(query: str) -> dict | None:
    """Resolve a free-form identifier (name, login, email, JIRA displayName)
    to a single unified_users record. Returns None if no match.
    """
    if not query:
        return None
    q = query.strip()
    q_lower = q.lower()
    coll = unified_users_collection()

    if "@" in q_lower:
        d = coll.find_one({"email": q_lower}, {"_id": 0})
        if d:
            return d

    d = coll.find_one({"github.login": q}, {"_id": 0})
    if d:
        return d

    if ":" in q and len(q) > 20:
        d = coll.find_one({"jira.accountId": q}, {"_id": 0})
        if d:
            return d

    d = coll.find_one({"jira.displayName": q}, {"_id": 0})
    if d:
        return d

    rx = {"$regex": re.escape(q), "$options": "i"}
    d = coll.find_one(
        {
            "$or": [
                {"jira.displayName": rx},
                {"github.name": rx},
                {"github.login": rx},
            ]
        },
        {"_id": 0},
    )
    return d


def _identity(value: str, identity_path: str | None) -> str:
    """Translate a person reference to one source's stored identity string.

    `identity_path` is a dotted path into the unified_users record (e.g.
    "jira.displayName", "github.login"). Falls back to the raw value when the
    user can't be resolved, so an exact stored name still matches.
    """
    if not identity_path:
        return value
    user = _resolve_user_doc(value)
    if not user:
        return value
    node: Any = user
    for part in identity_path.split("."):
        if not isinstance(node, dict):
            return value
        node = node.get(part)
    return node or value


# Tool definitions live in sibling modules; importing them registers the @tool
# objects. `discover_tools` then assembles the toolkit by walking this package.
from agent_yoku.agent.tools._registry import discover_tools  # noqa: E402
from agent_yoku.agent.tools.filter import filter  # noqa: E402,F401
from agent_yoku.agent.tools.freshness import data_freshness  # noqa: E402,F401
from agent_yoku.agent.tools.get import get  # noqa: E402,F401
from agent_yoku.agent.tools.linked import linked  # noqa: E402,F401
from agent_yoku.agent.tools.mongo import (  # noqa: E402,F401
    _validate_pipeline,
    describe_collection,
    list_collections,
    mongo_count,
    mongo_query,
)
from agent_yoku.agent.tools.resolve_user import resolve_user  # noqa: E402,F401
from agent_yoku.agent.tools.semantic_search import semantic_search  # noqa: E402,F401
from agent_yoku.agent.tools.who_knows import who_knows  # noqa: E402,F401


def get_all_tools() -> list[BaseTool]:
    """The agent's full toolkit, assembled by auto-discovery.

    Source-agnostic: `filter`, `get`, `linked`, and `semantic_search` all work
    across every registered source, so adding a connector needs no change here —
    and adding a tool file is picked up without editing any list.
    """
    return discover_tools()


#: The agent's full toolkit (kept importable for back-compat; computed via
#: discovery so it stays in sync with the tool modules on disk).
ALL_TOOLS = get_all_tools()
