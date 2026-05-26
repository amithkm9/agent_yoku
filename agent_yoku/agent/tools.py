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
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from langchain_core.tools import tool

from agent_yoku.config import (
    ALLOWED_COLLECTIONS,
    EMBED_MODEL,
    get_collection,
    github_prs_collection,
    openai_client,
    tickets_collection,
    unified_users_collection,
)
from agent_yoku.log import get_logger
from agent_yoku.storage.freshness import source_freshness
from agent_yoku.utils import bson_safe

log = get_logger("tools")

# ---------- Lazy-loaded singleton hybrid index (vector + keyword) ----------
_INDEX_LOCK = threading.Lock()
_INDEX: dict[str, Any] = {"loaded": False}


def _ensure_index() -> None:
    """Load the unified JIRA + PR embedding matrix once per process."""
    if _INDEX["loaded"]:
        return
    with _INDEX_LOCK:
        if _INDEX["loaded"]:
            return
        log.info("loading unified embedding index…")
        proj = {
            "key": 1,
            "summary": 1,
            "status": 1,
            "assignee": 1,
            "url": 1,
            "embedding": 1,
            "jira_keys": 1,
            "repo": 1,
        }

        jira = list(tickets_collection().find({"embedding": {"$ne": None, "$exists": True}}, proj))
        for d in jira:
            d["source"] = "jira"

        gh = list(github_prs_collection().find({"embedding": {"$ne": None, "$exists": True}}, proj))
        for d in gh:
            d["source"] = "github"

        docs = jira + gh
        if not docs:
            raise RuntimeError("no embedded docs found; run ingest + embed first")

        matrix = np.array([d["embedding"] for d in docs], dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
        for d in docs:
            d.pop("embedding", None)
            d.pop("_id", None)

        # Keyword half of hybrid search: an inverted index over identifier +
        # summary tokens, idf-weighted so rare tokens (a ticket key, a repo
        # name) outweigh common words.
        inverted: dict[str, list[int]] = {}
        for i, d in enumerate(docs):
            for tok in _tokenize(_doc_keyword_text(d)):
                inverted.setdefault(tok, []).append(i)
        # Drop ubiquitous tokens (the `as` project prefix, `asatocorp` org) — they
        # match nearly everything and let vector-top docs piggyback a keyword hit,
        # drowning out the true exact match. Absolute floor keeps tiny corpora intact.
        stop_df = max(_STOP_DF_MIN, int(_STOP_DF_RATIO * len(docs)))
        inverted = {tok: post for tok, post in inverted.items() if len(post) <= stop_df}
        idf = {tok: float(np.log(1.0 + len(docs) / len(post))) for tok, post in inverted.items()}

        _INDEX["docs"] = docs
        _INDEX["matrix"] = matrix
        _INDEX["inverted"] = inverted
        _INDEX["idf"] = idf
        _INDEX["loaded"] = True
        log.info("index loaded jira=%d github=%d", len(jira), len(gh))


def _embed_query(text: str) -> np.ndarray:
    resp = openai_client().embeddings.create(model=EMBED_MODEL, input=[text])
    v = np.array(resp.data[0].embedding, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-12)


def _clean(doc: dict, drop: tuple[str, ...] = ()) -> dict:
    """Strip _id + caller-named fields, then recursively JSON-safe the rest.

    Mongo can return nested ObjectId / datetime values (e.g. JIRA's
    `_synced_at`, the linked_prs blobs). Without bson_safe, those crash the
    LangChain tool serializer mid-loop. Cheap and always-safe to apply.
    """
    out = {k: v for k, v in doc.items() if k not in drop and k != "_id"}
    return bson_safe(out)


# ---------- Hybrid search internals (keyword + RRF) ----------

# Tokens are kept whole (``as-1234``, ``dc-okta-user``, ``asatocorp/repo#12``)
# and also split into pieces, so a query for "okta" still hits "dc-okta-user".
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/#._][a-z0-9]+)*")
_SPLIT_RE = re.compile(r"[-/#._]+")
_RRF_K = 60  # rank-fusion damping constant (the RRF paper's default).
_KEYWORD_WEIGHT = 2.0  # bias fusion toward exact-token hits (keys, repos, names).
_STOP_DF_RATIO = 0.1  # tokens in >10% of docs are noise...
_STOP_DF_MIN = 100  # ...but only once the corpus is large enough to judge.


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
            str(doc.get("repo") or ""),
            str(doc.get("assignee") or ""),
            " ".join(str(x) for x in (doc.get("jira_keys") or [])),
        ]
    )


def _keyword_scores(query: str) -> dict[int, float]:
    """idf-weighted token-overlap score, keyed by doc index (matches only)."""
    inverted = _INDEX["inverted"]
    idf = _INDEX["idf"]
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


# =================== SEMANTIC ===================


@tool
def semantic_search(query: str, k: int = 50, source: str = "both") -> list[dict]:
    """Hybrid (vector + keyword) search over the unified JIRA + GitHub PR index.

    Combines cosine similarity (meaning) with an idf-weighted keyword match
    (exact tokens — ticket keys, repo names, people, error strings) and fuses
    the two rankings with Reciprocal Rank Fusion. This surfaces exact-identifier
    hits that pure vector search ranks too low, without hurting semantic recall.

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
    _ensure_index()
    k = max(1, min(int(k), 200))
    if source not in ("jira", "github", "both"):
        raise ValueError(f"source must be jira/github/both, got {source!r}")

    docs = _INDEX["docs"]
    matrix = _INDEX["matrix"]
    cos = matrix @ _embed_query(query)  # cosine vs every doc (matrix is normalized)

    # Fuse more candidates than k from each ranking, so a strong keyword hit
    # outside the vector top-k still reaches the fusion step.
    pool = max(k * 5, 100)

    # Vector ranking (optionally restricted to one source).
    if source == "both":
        vec_order = np.argsort(-cos)[:pool].tolist()
    else:
        elig = [i for i, d in enumerate(docs) if d["source"] == source]
        elig.sort(key=lambda i: -cos[i])
        vec_order = elig[:pool]

    # Keyword ranking, restricted to the same source.
    kw = {
        i: s
        for i, s in _keyword_scores(query).items()
        if source == "both" or docs[i]["source"] == source
    }
    kw_order = sorted(kw, key=lambda i: -kw[i])[:pool]

    fused = _rrf_fuse([vec_order, kw_order], weights=[1.0, _KEYWORD_WEIGHT])
    ranked = sorted(fused, key=lambda i: -fused[i])[:k]

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


# =================== USER RESOLUTION ===================


def _resolve_user_doc(query: str) -> dict | None:
    """Resolve a free-form identifier (name, login, email, JIRA displayName)
    to a single unified_users record. Returns None if no match.
    """
    if not query:
        return None
    q = query.strip()
    q_lower = q.lower()
    coll = unified_users_collection()

    # 1. Exact email
    if "@" in q_lower:
        d = coll.find_one({"email": q_lower}, {"_id": 0})
        if d:
            return d

    # 2. Exact GH login
    d = coll.find_one({"github.login": q}, {"_id": 0})
    if d:
        return d

    # 3. Exact JIRA accountId
    if ":" in q and len(q) > 20:
        d = coll.find_one({"jira.accountId": q}, {"_id": 0})
        if d:
            return d

    # 4. Exact JIRA displayName
    d = coll.find_one({"jira.displayName": q}, {"_id": 0})
    if d:
        return d

    # 5. Case-insensitive partial match on displayName / name / login.
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


# =================== POINT LOOKUPS ===================


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
            raise ValueError(f"PR {key!r} not found")
        return _clean(doc)
    doc = tickets_collection().find_one({"key": key}, {"embedding": 0})
    if not doc:
        raise ValueError(f"JIRA ticket {key!r} not found")
    return _clean(doc)


# =================== CROSS-SOURCE LINKS ===================


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


# =================== STRUCTURED FILTERS ===================


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
        q["jira_keys"] = []
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


# =================== STATS ===================
#
# Counts are intentionally not narrow tools: `mongo_count` (generic escape
# hatch) covers any count over either collection with arbitrary filters.


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


# =================== GENERIC MONGO ESCAPE HATCH ===================
#
# Pattern lifted from asato-api MCP (~/Desktop/asato-api/asatoapi/mcp/tools):
# give the agent a small set of generic, read-only mongo primitives so it can
# compose arbitrary queries the narrow tools above don't cover. The narrow
# tools stay as the fast/safe path for common cases; these are the power
# escape hatch.

_COLLECTION_DESCRIPTIONS = {
    "jira_tickets": "Asato JIRA tickets (project AS). Fields: key, summary, description, status, issuetype, assignee, reporter, priority, labels, fix_versions, created, updated, url, linked_prs.",
    "github_prs": "AsatoCorp GitHub PRs. Fields: key (org/repo#N), repo, number, summary, description, status (open|closed|merged|draft), author, author_email, assignee, labels, base, head, merged, merged_at, comments_count, created, updated, url, jira_keys.",
    "users": "JIRA users directory. Fields: accountId, displayName, emailAddress, active, accountType.",
    "github_users": "AsatoCorp GitHub org members. Fields: login, id, name, email, is_bot, type, company.",
    "unified_users": "Cross-walk between JIRA + GitHub users. Fields: user_id, email, jira.accountId, jira.displayName, github.login, github.name, is_bot, match_source.",
}

# Aggregation stages that perform writes or run arbitrary code — blocked.
_BLOCKED_STAGES = {"$out", "$merge", "$function", "$accumulator", "$where"}

_MAX_PIPELINE_STAGES = 20
_MAX_INLINE_RESULTS = 100
_HIDDEN_FIELDS = {"embedding", "text"}  # token-budget killers

# Fields that should never appear in describe_collection samples (PII / huge).
_DESCRIBE_DROP_FIELDS = {"_id", "embedding", "text", "description"}


def _validate_pipeline(pipeline: list[dict]) -> None:
    if not isinstance(pipeline, list) or not pipeline:
        raise ValueError("pipeline must be a non-empty list of stage objects")
    if len(pipeline) > _MAX_PIPELINE_STAGES:
        raise ValueError(f"pipeline has {len(pipeline)} stages; max is {_MAX_PIPELINE_STAGES}")
    for i, stage in enumerate(pipeline):
        if not isinstance(stage, dict) or len(stage) != 1:
            raise ValueError(f"stage {i} must be a single-key dict {{'$op': {{...}}}}")
        op = next(iter(stage))
        if op in _BLOCKED_STAGES:
            raise ValueError(f"stage {i} uses blocked operator {op!r}")
        if not op.startswith("$"):
            raise ValueError(f"stage {i} key {op!r} is not a Mongo operator (missing $)")


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

    # Ensure a $limit stage exists and is within bounds.
    has_limit = False
    for stage in pipeline:
        if "$limit" in stage:
            stage["$limit"] = min(int(stage["$limit"]), capped_limit)
            has_limit = True
            break
    if not has_limit:
        pipeline = [*pipeline, {"$limit": capped_limit}]

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


# ---------- Tool groupings for the deepagent ----------

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
    get,
    filter_jira,
    filter_prs,
    linked,
    list_repos,
    data_freshness,
    *GENERIC_MONGO_TOOLS,
]
