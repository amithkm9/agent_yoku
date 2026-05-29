"""Tools the deepagent uses to navigate JIRA tickets + GitHub PRs in mongo.

Design principles:
- Each tool is a thin wrapper around mongo or numpy. No LLM calls inside tools.
- Tools return JSON-serializable data (no ObjectId, no datetime objects).
- Lightweight by default; the agent calls get_* tools to drill into specific items.
- Errors raise ValueError with a clear message so the agent can adapt.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import numpy as np
from langchain_core.tools import ToolException, tool

from agent_yoku.agent.rerank import rerank
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
from agent_yoku.storage.freshness import source_freshness
from agent_yoku.storage.mongo import slack_messages_collection
from agent_yoku.storage.tenancy import current_tenant
from agent_yoku.utils import bson_safe

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


def _to_epoch(value: str | datetime | None) -> float | None:
    """Parse an ISO-8601 timestamp (JIRA/GitHub `updated`) to epoch seconds, or
    None if absent/unparsable — callers treat None as 'age unknown'."""
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return None


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

    jira = list(tickets_collection().find({"embedding": {"$ne": None, "$exists": True}}, proj))
    for d in jira:
        d["source"] = "jira"

    gh = list(github_prs_collection().find({"embedding": {"$ne": None, "$exists": True}}, proj))
    for d in gh:
        d["source"] = "github"

    slack = list(
        slack_messages_collection().find({"embedding": {"$ne": None, "$exists": True}}, proj)
    )
    for d in slack:
        d["source"] = "slack"

    docs = jira + gh + slack
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
        if d["source"] in ("github", "slack"):
            d["link_keys"] = list(d.get("jira_keys") or [])
        else:
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

    log.info("index loaded jira=%d github=%d slack=%d", len(jira), len(gh), len(slack))
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


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/#._][a-z0-9]+)*")
_SPLIT_RE = re.compile(r"[-/#._]+")
_RRF_K = 60
_KEYWORD_WEIGHT = 2.0
_STOP_DF_RATIO = 0.1
_STOP_DF_MIN = 100


def _tokenize(text: str) -> set[str]:
    toks: set[str] = set()
    for whole in _TOKEN_RE.findall(text.lower()):
        toks.add(whole)
        toks.update(p for p in _SPLIT_RE.split(whole) if p)
    return toks


def _doc_keyword_text(doc: dict) -> str:
    """The fields worth exact-matching: identifiers plus the human summary."""
    return " ".join(
        [
            str(doc.get("key") or ""),
            str(doc.get("summary") or ""),
            str(doc.get("repo") or doc.get("channel_name") or ""),
            str(doc.get("assignee") or doc.get("author_name") or ""),
            " ".join(str(x) for x in (doc.get("jira_keys") or [])),
        ]
    )


def _keyword_scores(query: str, idx: dict[str, Any]) -> dict[int, float]:
    """idf-weighted token-overlap score, keyed by doc index (matches only)."""
    inverted = idx["inverted"]
    idf = idx["idf"]
    scores: dict[int, float] = {}
    for tok in _tokenize(query):
        weight = idf.get(tok)
        if weight is None:
            continue
        for i in inverted[tok]:
            scores[i] = scores.get(i, 0.0) + weight
    return scores


def _rrf_fuse(
    ranked_lists: list[list[int]], weights: list[float] | None = None
) -> dict[int, float]:
    """Weighted Reciprocal Rank Fusion: score = Σ wᵢ/(K + rank), rank from 1.

    Rank-based, so it needs no normalization between the wildly different
    cosine and idf-overlap scales — it only cares about position in each list.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    fused: dict[int, float] = {}
    for ranked, w in zip(ranked_lists, weights):
        for rank, i in enumerate(ranked, start=1):
            fused[i] = fused.get(i, 0.0) + w / (_RRF_K + rank)
    return fused


_RERANK_MIN_WINDOW = 30
_W_LEX = 0.05
_W_CORR = 0.02
_W_ADJ = 0.15
_W_RECENCY = 0.03
_RECENCY_HALFLIFE_DAYS = 180.0
_WHO_KNOWS_RECENCY_FLOOR = 0.2


def _lexical_overlap(query: str, query_tokens: set[str], summary: str) -> float:
    """0..1 signal: token overlap with the summary, plus a phrase-match bonus."""
    summ = (summary or "").lower()
    overlap = (len(query_tokens & _tokenize(summ)) / len(query_tokens)) if query_tokens else 0.0
    phrase = 0.5 if query.strip().lower() in summ else 0.0
    return min(1.0, overlap + phrase)


def _recency_factor(updated_ts: float | None, now: float) -> float:
    """1.0 for a just-updated item, halving every _RECENCY_HALFLIFE_DAYS; 0.0 when
    the timestamp is unknown (neutral — no boost, never a penalty below the base)."""
    if updated_ts is None:
        return 0.0
    age_days = max(0.0, (now - updated_ts) / 86400.0)
    return 0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS)


def _adjacency_scores(fused: dict[int, float], idx: dict[str, Any]) -> dict[int, float]:
    """For each fused candidate, how relevant its strongest linked counterpart is.

    A cross-source link (PR <-> ticket) is evidence: if a candidate's linked item
    also surfaced for this query, the candidate is more likely the answer. Returns
    {doc_index: 0..1} — the best linked counterpart's fused score scaled by the top
    fused score, so a candidate whose counterpart is the #1 hit scores ~1.0. Only
    counterparts that are themselves in `fused` count; absent links contribute 0.
    """
    if not fused:
        return {}
    docs = idx["docs"]
    key_to_idx = idx["key_to_idx"]
    top = max(fused.values())
    out: dict[int, float] = {}
    for i in fused:
        best = 0.0
        for k in docs[i].get("link_keys") or ():
            j = key_to_idx.get(k)
            if j is not None and j in fused:
                best = max(best, fused[j])
        if best > 0.0:
            out[i] = best / top
    return out


def _feature_rerank(
    query: str,
    candidates: list[tuple[int, float]],
    idx: dict[str, Any],
    adjacency: dict[int, float],
) -> list[int]:
    """Rescore first-stage (doc_index, fused_score) pairs with richer signals.

    Refines — not replaces — fusion: the fused score is the base, scaled by a
    small multiplicative boost from lexical overlap with the summary, cross-source
    corroboration, query-aware link adjacency (a linked counterpart that itself
    surfaced for this query), and recency (freshly-updated items rank ahead of
    stale ones). Multiplicative keeps the boost proportional, so it reorders
    near-ties and modest gaps without letting a weakly-matched item leapfrog one
    far ahead in fusion. This is the seam where a cross-encoder or hosted reranker
    could drop in later.
    """
    qtokens = _tokenize(query)
    docs = idx["docs"]
    now = time.time()
    scored = []
    for i, base in candidates:
        d = docs[i]
        lex = _lexical_overlap(query, qtokens, d.get("summary") or "")
        corr = 1.0 if d.get("has_links") else 0.0
        adj = adjacency.get(i, 0.0)
        rec = _recency_factor(d.get("updated_ts"), now)
        boost = 1.0 + _W_LEX * lex + _W_CORR * corr + _W_ADJ * adj + _W_RECENCY * rec
        scored.append((i, base * boost))
    scored.sort(key=lambda x: -x[1])
    return [i for i, _ in scored]


def _fetch_texts(keys: list[str]) -> dict[str, str]:
    """Pull the full embedded `text` for the given doc keys, split by key shape."""
    jira = [k for k in keys if not _is_pr_key(k)]
    prs = [k for k in keys if _is_pr_key(k)]
    out: dict[str, str] = {}
    if jira:
        for d in tickets_collection().find({"key": {"$in": jira}}, {"_id": 0, "key": 1, "text": 1}):
            out[d["key"]] = d.get("text") or ""
    if prs:
        for d in github_prs_collection().find(
            {"key": {"$in": prs}}, {"_id": 0, "key": 1, "text": 1}
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


@tool
def semantic_search(query: str, k: int = 50, source: str = "both") -> list[dict]:
    """Hybrid (vector + keyword) search over the unified JIRA + GitHub PR index.

    Combines cosine similarity (meaning) with an idf-weighted keyword match
    (exact tokens — ticket keys, repo names, people, error strings) and fuses
    the two rankings with Reciprocal Rank Fusion. This surfaces exact-identifier
    hits that pure vector search ranks too low, without hurting semantic recall.
    A second-stage reranker (ZeroEntropy when configured, otherwise a built-in
    feature reranker) then refines the top window — using cross-source link
    adjacency and recency as additional signals — before returning k results.

    Returns lightweight cards sorted by relevance. Use this to *discover*
    candidate items, then call get(key) to read full content.

    Args:
        query: Natural-language or identifier search string.
        k: Number of results to return. Default 50, max 200.
        source: "jira" | "github" | "both" (default).

    Returns:
        List of {key, summary, status, assignee, score, url, source, jira_keys?, repo?}.
        `score` is the cosine similarity; ordering is the fused rank.
    """
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    k = max(1, min(int(k), 200))
    if source not in ("jira", "github", "slack", "both"):
        raise ValueError(f"source must be jira/github/slack/both, got {source!r}")

    idx = _index()
    docs = idx["docs"]
    matrix = idx["matrix"]
    cos = matrix @ _embed_query(query)

    pool = max(k * 5, 100)

    if source == "both":
        vec_order = np.argsort(-cos)[:pool].tolist()
    else:
        elig = [i for i, d in enumerate(docs) if d["source"] == source]
        elig.sort(key=lambda i: -cos[i])
        vec_order = elig[:pool]

    kw = {
        i: s
        for i, s in _keyword_scores(query, idx).items()
        if source == "both" or docs[i]["source"] == source
    }
    kw_order = sorted(kw, key=lambda i: -kw[i])[:pool]

    fused = _rrf_fuse([vec_order, kw_order], weights=[1.0, _KEYWORD_WEIGHT])
    adjacency = _adjacency_scores(fused, idx)

    window = sorted(fused, key=lambda i: -fused[i])[: max(k, _RERANK_MIN_WINDOW)]
    ranked = _rerank_order(query, window, fused, idx, adjacency)[:k]

    out = []
    for i in ranked:
        d = docs[i]
        card = {
            "key": d["key"],
            "summary": d.get("summary"),
            "status": d.get("status"),
            "assignee": d.get("assignee"),
            "url": d.get("url"),
            "source": d["source"],
            "score": round(float(cos[i]), 4),
        }
        if d["source"] == "github":
            card["repo"] = d.get("repo")
            card["jira_keys"] = d.get("jira_keys") or []
        out.append(card)
    return out


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


@tool
def resolve_user(query: str) -> dict:
    """Resolve a free-form user reference (name, GitHub login, email, or JIRA
    displayName) into a unified user record with both JIRA and GitHub identifiers.

    Use this before calling filter_jira(assignee=…) or filter_prs(author=…) when
    the user references someone by partial name or alias — it returns the exact
    strings needed for those filters.

    Returns a dict with: email, jira.displayName, jira.accountId, github.login,
    is_bot, match_source. Raises ValueError if no user matches.
    """
    doc = _resolve_user_doc(query)
    if not doc:
        raise ValueError(f"no user matches {query!r}")
    return doc


def _is_pr_key(key: str) -> bool:
    """PR keys look like 'org/repo#123'; JIRA keys look like 'AS-4163'.

    The '#' separator is unique to PR keys, so it disambiguates the two sources.
    """
    return "#" in key


@tool
def get(key: str) -> dict:
    """Fetch a single JIRA ticket or GitHub PR by key, auto-routed by key shape.

    - JIRA ticket key (e.g. 'AS-4163') → full ticket fields including the
      description text and `linked_prs` (PRs that reference this ticket).
    - GitHub PR key (e.g. 'AsatoCorp/agent-svc#173') → full PR fields including
      body, branch, status, jira_keys, etc.

    Raises ValueError if no item matches the key.
    """
    if _is_pr_key(key):
        doc = github_prs_collection().find_one({"key": key}, {"embedding": 0})
        if not doc:
            raise ToolException(f"PR {key!r} not found")
        return _clean(doc)
    doc = tickets_collection().find_one({"key": key}, {"embedding": 0})
    if not doc:
        raise ToolException(f"JIRA ticket {key!r} not found")
    return _clean(doc)


def _prs_for_jira(jira_key: str, limit: int) -> list[dict]:
    cursor = (
        github_prs_collection()
        .find(
            {"jira_keys": jira_key},
            {
                "key": 1,
                "repo": 1,
                "summary": 1,
                "status": 1,
                "url": 1,
                "author": 1,
                "merged": 1,
                "updated": 1,
            },
        )
        .limit(int(limit))
    )
    return [_clean(d) for d in cursor]


def _jira_for_pr(pr_key: str) -> list[dict]:
    pr = github_prs_collection().find_one({"key": pr_key}, {"jira_keys": 1})
    if not pr:
        raise ValueError(f"PR {pr_key!r} not found")
    keys = pr.get("jira_keys") or []
    if not keys:
        return []
    cursor = tickets_collection().find(
        {"key": {"$in": keys}},
        {"key": 1, "summary": 1, "status": 1, "assignee": 1, "url": 1},
    )
    return [_clean(d) for d in cursor]


@tool
def linked(key: str, limit: int = 20) -> list[dict]:
    """Cross-source link hop, auto-routed by key shape.

    - JIRA key (e.g. 'AS-4163') → GitHub PRs whose branch/title/body references
      it. Use when you have a ticket and need the code that addresses it.
    - PR key (e.g. 'AsatoCorp/agent-svc#173') → JIRA tickets the PR references.

    `limit` applies only to the JIRA → PRs direction.
    """
    if _is_pr_key(key):
        return _jira_for_pr(key)
    return _prs_for_jira(key, limit)


def _since(days: int | None) -> datetime | None:
    if not days:
        return None
    return datetime.now(UTC) - timedelta(days=int(days))


@tool
def filter_jira(
    status: str | None = None,
    assignee: str | None = None,
    label: str | None = None,
    issuetype: str | None = None,
    epic_key: str | None = None,
    fix_version: str | None = None,
    since_days: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Filter JIRA tickets by exact criteria (no semantic search).

    Args:
        status: e.g. 'To Do', 'In Progress', 'Done', 'Testing'.
        assignee: JIRA displayName, GitHub login, or email — auto-resolved
                  via unified_users.
        label: any label, e.g. 'apr-bug-bash'.
        issuetype: 'Task', 'Story', 'Bug', 'Epic'.
        epic_key: parent epic key, e.g. 'AS-1000' — returns issues directly
                  under that epic (Sub-tasks roll up via their Story, not here).
        fix_version: target release name, e.g. 'Sprint 24' or 'v2.5'.
        since_days: only tickets updated within last N days.
        limit: max results, default 20.
    """
    q: dict = {}
    if status:
        q["status"] = status
    if assignee:
        u = _resolve_user_doc(assignee)
        jira_name = (u.get("jira") or {}).get("displayName") if u else None
        q["assignee"] = jira_name or assignee
    if label:
        q["labels"] = label
    if issuetype:
        q["issuetype"] = issuetype
    if epic_key:
        q["epic_key"] = epic_key
    if fix_version:
        q["fix_versions"] = fix_version
    since = _since(since_days)
    if since:
        q["updated"] = {"$gte": since.isoformat()}
    cursor = (
        tickets_collection()
        .find(
            q,
            {
                "key": 1,
                "summary": 1,
                "status": 1,
                "assignee": 1,
                "issuetype": 1,
                "epic_key": 1,
                "parent_key": 1,
                "labels": 1,
                "updated": 1,
                "url": 1,
            },
        )
        .sort("updated", -1)
        .limit(int(limit))
    )
    return [_clean(d) for d in cursor]


@tool
def filter_prs(
    repo: str | None = None,
    status: str | None = None,
    author: str | None = None,
    has_jira_link: bool | None = None,
    since_days: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Filter GitHub PRs by exact criteria.

    Args:
        repo: 'AsatoCorp/agent-svc' (full name) or 'agent-svc' (short).
        status: 'open' | 'closed' | 'merged' | 'draft'.
        author: GitHub login, JIRA displayName, or email — auto-resolved
                via unified_users.
        has_jira_link: True to return only PRs with at least one jira_keys entry.
        since_days: PRs updated within last N days.
        limit: max results, default 20.
    """
    q: dict = {}
    if repo:
        q["repo"] = repo if "/" in repo else f"AsatoCorp/{repo}"
    if status:
        q["status"] = status
    if author:
        u = _resolve_user_doc(author)
        gh_login = (u.get("github") or {}).get("login") if u else None
        q["author"] = gh_login or author
    if has_jira_link is True:
        q["jira_keys"] = {"$ne": []}
    elif has_jira_link is False:
        q["jira_keys"] = {"$in": [[], None]}
    since = _since(since_days)
    if since:
        q["updated"] = {"$gte": since.isoformat()}
    cursor = (
        github_prs_collection()
        .find(
            q,
            {
                "key": 1,
                "repo": 1,
                "summary": 1,
                "status": 1,
                "author": 1,
                "jira_keys": 1,
                "updated": 1,
                "url": 1,
                "merged": 1,
            },
        )
        .sort("updated", -1)
        .limit(int(limit))
    )
    return [_clean(d) for d in cursor]


@tool
def filter_slack(
    channel: str | None = None,
    author: str | None = None,
    has_jira_link: bool | None = None,
    since_days: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Filter Slack messages by channel, author, JIRA link presence, or recency.

    Args:
        channel: channel name without the # prefix, e.g. 'engineering'.
        author: display name, GitHub login, or email — auto-resolved via unified_users.
        has_jira_link: True to return only messages that mention a JIRA ticket key.
        since_days: only messages from the last N days.
        limit: max results, default 20.
    """
    q: dict = {}
    if channel:
        q["channel_name"] = channel
    if author:
        u = _resolve_user_doc(author)
        slack_name = None
        if u:
            identities = u.get("slack") or {}
            slack_name = identities.get("display_name")
        q["author_name"] = slack_name or author
    if has_jira_link is True:
        q["jira_keys"] = {"$ne": []}
    elif has_jira_link is False:
        q["jira_keys"] = {"$in": [[], None]}
    since = _since(since_days)
    if since:
        q["updated"] = {"$gte": since.isoformat()}
    cursor = (
        slack_messages_collection()
        .find(
            q,
            {
                "key": 1,
                "channel_name": 1,
                "author_name": 1,
                "summary": 1,
                "jira_keys": 1,
                "thread_ts": 1,
                "is_thread_reply": 1,
                "updated": 1,
                "url": 1,
            },
        )
        .sort("updated", -1)
        .limit(int(limit))
    )
    return [_clean(d) for d in cursor]


@tool
def list_repos(min_prs: int = 1, limit: int = 50) -> list[dict]:
    """List repos that have at least `min_prs` indexed PRs, sorted by count desc."""
    pipeline = [
        {"$group": {"_id": "$repo", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": int(min_prs)}}},
        {"$sort": {"n": -1}},
        {"$limit": int(limit)},
    ]
    return [
        {"repo": d["_id"], "pr_count": d["n"]} for d in github_prs_collection().aggregate(pipeline)
    ]


_COLLECTION_DESCRIPTIONS = {
    "jira_tickets": "Asato JIRA tickets (project AS). Fields: key, summary, description, status, issuetype, epic_key, parent_key, assignee, reporter, priority, labels, fix_versions, created, updated, url, linked_prs.",
    "github_prs": "AsatoCorp GitHub PRs. Fields: key (org/repo#N), repo, number, summary, description, status (open|closed|merged|draft), author, author_email, assignee, labels, base, head, merged, merged_at, comments_count, created, updated, url, jira_keys.",
    "users": "JIRA users directory. Fields: accountId, displayName, emailAddress, active, accountType.",
    "github_users": "AsatoCorp GitHub org members. Fields: login, id, name, email, is_bot, type, company.",
    "unified_users": "Cross-walk between JIRA + GitHub users. Fields: user_id, email, jira.accountId, jira.displayName, github.login, github.name, is_bot, match_source.",
}

_BLOCKED_STAGES = {"$out", "$merge", "$function", "$accumulator", "$where"}

# Stages that read a second collection: their target is checked against the read
# whitelist so the agent can't $lookup/$unionWith into auth_users or connector_configs.
_CROSS_COLLECTION_STAGES = {"$lookup", "$unionWith", "$graphLookup"}

_MAX_PIPELINE_STAGES = 20
_MAX_INLINE_RESULTS = 100
_HIDDEN_FIELDS = {"embedding", "text"}
_DESCRIBE_DROP_FIELDS = {"_id", "embedding", "text", "description"}


def _cross_collection_target(op: str, spec: Any) -> str | None:
    """The foreign collection a cross-collection stage reads from, if any."""
    if op == "$unionWith":
        return spec if isinstance(spec, str) else (spec or {}).get("coll")
    if isinstance(spec, dict):  # $lookup / $graphLookup name it in `from`
        return spec.get("from")
    return None


def _validate_stage(stage: Any, i: int) -> None:
    if not isinstance(stage, dict) or len(stage) != 1:
        raise ValueError(f"stage {i} must be a single-key dict {{'$op': {{...}}}}")
    op = next(iter(stage))
    if not op.startswith("$"):
        raise ValueError(f"stage {i} key {op!r} is not a Mongo operator (missing $)")
    if op in _BLOCKED_STAGES:
        raise ValueError(f"stage {i} uses blocked operator {op!r}")
    spec = stage[op]
    if op in _CROSS_COLLECTION_STAGES:
        target = _cross_collection_target(op, spec)
        if not isinstance(target, str) or target not in ALLOWED_COLLECTIONS:
            raise ValueError(
                f"stage {i} {op} reads collection {target!r}, which is not on the read "
                f"whitelist {sorted(ALLOWED_COLLECTIONS)}"
            )
    # Recurse into nested sub-pipelines so they can't smuggle a blocked stage.
    if isinstance(spec, dict):
        nested = spec.get("pipeline")
        if isinstance(nested, list):
            for j, sub in enumerate(nested):
                _validate_stage(sub, j)
    if op == "$facet" and isinstance(spec, dict):
        for sub_pipeline in spec.values():
            if isinstance(sub_pipeline, list):
                for j, sub in enumerate(sub_pipeline):
                    _validate_stage(sub, j)


def _validate_pipeline(pipeline: list[dict]) -> None:
    if not isinstance(pipeline, list) or not pipeline:
        raise ValueError("pipeline must be a non-empty list of stage objects")
    if len(pipeline) > _MAX_PIPELINE_STAGES:
        raise ValueError(f"pipeline has {len(pipeline)} stages; max is {_MAX_PIPELINE_STAGES}")
    for i, stage in enumerate(pipeline):
        _validate_stage(stage, i)


@tool
def list_collections() -> list[dict] | dict:
    """Enumerate the mongo collections the agent may read from.

    Returns each collection's name, current document count, and a one-line
    description of its purpose. Use this when you don't know which collection
    holds the data you need.
    """
    try:
        out = []
        for name, factory in ALLOWED_COLLECTIONS.items():
            coll = factory()
            out.append(
                {
                    "name": name,
                    "count": coll.estimated_document_count(),
                    "description": _COLLECTION_DESCRIPTIONS.get(name, ""),
                }
            )
        return out
    except Exception as e:
        return {"error": f"list_collections failed: {e}"}


@tool
def describe_collection(collection: str, sample_size: int = 20) -> dict:
    """Sample N documents from a collection and report which fields appear and
    what types they have. Useful before composing a `mongo_query` to make sure
    you reference field names that actually exist.

    Args:
        collection: name from list_collections() (e.g. "jira_tickets",
            "github_prs", "unified_users").
        sample_size: how many docs to sample (default 20, max 100).

    Returns:
        {"collection", "sampled", "fields": {field: {"types": [...], "coverage": float}}}
        or {"error": str} on failure.
    """
    try:
        coll = get_collection(collection)
    except ValueError as e:
        return {"error": str(e), "allowed": sorted(ALLOWED_COLLECTIONS)}
    n = max(1, min(int(sample_size), 100))
    cursor = coll.aggregate([{"$sample": {"size": n}}])
    fields: dict[str, dict] = {}
    sampled = 0
    for doc in cursor:
        sampled += 1
        for k, v in doc.items():
            if k in _DESCRIBE_DROP_FIELDS:
                continue
            t = "null" if v is None else type(v).__name__
            entry = fields.setdefault(k, {"types": set(), "count": 0})
            entry["types"].add(t)
            entry["count"] += 1
    out_fields = {
        k: {
            "types": sorted(v["types"]),
            "coverage": round(v["count"] / sampled, 2) if sampled else 0.0,
        }
        for k, v in sorted(fields.items())
    }
    return {"collection": collection, "sampled": sampled, "fields": out_fields}


@tool
def mongo_count(collection: str, filter: dict | None = None) -> dict:
    """Count documents in a collection matching an optional filter.

    Args:
        collection: name from list_collections() (e.g. "jira_tickets",
            "github_prs", "unified_users").
        filter: standard MongoDB find filter, e.g. {"status": "merged",
            "jira_keys": {"$ne": []}}. Omit or pass {} for a fast estimated
            count of the whole collection.

    Returns:
        {"count": int, "estimated": bool} or {"error": str}.
    """
    try:
        coll = get_collection(collection)
    except ValueError as e:
        return {"error": str(e), "allowed": sorted(ALLOWED_COLLECTIONS)}
    try:
        if not filter:
            return {"count": coll.estimated_document_count(), "estimated": True}
        return {"count": coll.count_documents(filter), "estimated": False}
    except Exception as e:
        return {"error": f"mongo_count failed: {e}"}


@tool
def mongo_query(
    collection: str,
    pipeline: list[dict],
    limit: int = 100,
) -> dict:
    """Run a read-only aggregation pipeline on one collection.

    Use this for ad-hoc queries the narrow tools (filter_jira / filter_prs /
    count_*) don't cover — e.g. grouping, joins via $lookup, custom projections,
    or filters on fields like fix_versions / labels / nested jira_keys.

    Safety bounds (enforced server-side):
    - pipeline must be <= 20 stages, each a single-key {"$op": {...}} dict
    - blocked stages: $out, $merge, $function, $accumulator, $where
    - $lookup / $unionWith / $graphLookup may only target whitelisted collections
    - results capped at min(limit, 100); $limit auto-appended if absent
    - fields `embedding` and `text` are stripped from results to save tokens

    Pipeline shape: a list of single-key stage objects.
        GOOD: [{"$match": {"repo": "AsatoCorp/asato-svc"}},
               {"$group": {"_id": "$status", "n": {"$sum": 1}}}]
        BAD:  [{"$match": {...}, "fields": {...}}]  ← extra sibling key

    Args:
        collection: collection name from list_collections() (e.g. "jira_tickets",
            "github_prs", "unified_users").
        pipeline: list of mongo aggregation stages.
        limit: max docs to return (default 100, max 100).

    Returns:
        {"results": list[dict], "count": int, "pipeline": list[dict]}
        or {"error": str} on failure.
    """
    try:
        coll = get_collection(collection)
    except ValueError as e:
        return {"error": str(e), "allowed": sorted(ALLOWED_COLLECTIONS)}
    try:
        _validate_pipeline(pipeline)
    except ValueError as e:
        return {"error": f"invalid pipeline: {e}"}

    capped_limit = max(1, min(int(limit), _MAX_INLINE_RESULTS))

    capped_stages: list[dict] = []
    has_limit = False
    for stage in pipeline:
        if "$limit" in stage:
            capped_stages.append({"$limit": min(int(stage["$limit"]), capped_limit)})
            has_limit = True
        else:
            capped_stages.append(stage)
    if not has_limit:
        capped_stages = [*capped_stages, {"$limit": capped_limit}]
    pipeline = capped_stages

    try:
        raw = list(coll.aggregate(pipeline))
    except Exception as e:
        return {"error": f"aggregation failed: {e}", "pipeline": pipeline}

    cleaned = []
    for d in raw:
        for k in _HIDDEN_FIELDS:
            d.pop(k, None)
        cleaned.append(bson_safe(d))
    return {"results": cleaned, "count": len(cleaned), "pipeline": pipeline}


@tool
def data_freshness() -> list[dict]:
    """Report how current each data source is: document count, when it last
    synced, the relative age, and the last sync status.

    Call this before concluding a source has no data — an empty result can mean
    the source is unsynced or stale, not that the work doesn't exist. Surface
    the age in your answer (e.g. "no GitHub data — last synced 6 days ago")
    rather than guessing.
    """
    return source_freshness()


def _identity_maps() -> tuple[dict[str, dict], dict[str, dict]]:
    """Lookup tables {jira displayName -> unified user} and {gh login -> unified}."""
    by_jira: dict[str, dict] = {}
    by_gh: dict[str, dict] = {}
    for u in unified_users_collection().find({}, {"_id": 0, "embedding": 0}):
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

    cards = semantic_search.invoke({"query": topic, "k": 40, "source": "both"})
    jira_card_keys = [c["key"] for c in cards if c["source"] == "jira" and c.get("assignee")]
    gh_keys = [c["key"] for c in cards if c["source"] == "github"]

    jira_ts: dict[str, float | None] = {}
    if jira_card_keys:
        jira_ts = {
            d["key"]: _to_epoch(d.get("updated"))
            for d in tickets_collection().find(
                {"key": {"$in": jira_card_keys}}, {"_id": 0, "key": 1, "updated": 1}
            )
        }
    contributions: list[tuple[str, str, str, float | None]] = [
        ("jira", c["assignee"], c["key"], jira_ts.get(c["key"]))
        for c in cards
        if c["source"] == "jira" and c.get("assignee")
    ]
    if gh_keys:
        for d in github_prs_collection().find(
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


GENERIC_MONGO_TOOLS = [list_collections, describe_collection, mongo_count, mongo_query]

JIRA_TOOLS = [
    get,
    filter_jira,
    linked,
    resolve_user,
    semantic_search,
    data_freshness,
    *GENERIC_MONGO_TOOLS,
]
GITHUB_TOOLS = [
    get,
    filter_prs,
    linked,
    list_repos,
    resolve_user,
    semantic_search,
    data_freshness,
    *GENERIC_MONGO_TOOLS,
]
ALL_TOOLS = [
    semantic_search,
    resolve_user,
    who_knows,
    get,
    filter_jira,
    filter_prs,
    filter_slack,
    linked,
    list_repos,
    data_freshness,
    *GENERIC_MONGO_TOOLS,
]
