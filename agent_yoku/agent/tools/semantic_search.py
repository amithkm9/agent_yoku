"""`semantic_search` — hybrid (vector + keyword) retrieval over the unified index."""

from __future__ import annotations

import numpy as np
from langchain_core.tools import tool

from agent_yoku.agent import tools as _t
from agent_yoku.agent.relationships import outbound_relationships
from agent_yoku.agent.sources import embeddable_sources, get_source
from agent_yoku.agent.tools._ranking import (
    _KEYWORD_WEIGHT,
    _RERANK_MIN_WINDOW,
    _adjacency_scores,
    _keyword_scores,
    _rrf_fuse,
)


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
        source: a source name (e.g. "jira", "github", "slack") or "both" for all.

    Returns:
        List of {key, summary, status, assignee, score, url, source, …}.
        `score` is the cosine similarity; ordering is the fused rank.
    """
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    k = max(1, min(int(k), 200))
    valid_sources = {s.name for s in embeddable_sources()} | {"both"}
    if source not in valid_sources:
        raise ValueError(f"source must be one of {sorted(valid_sources)}, got {source!r}")

    idx = _t._index()
    docs = idx["docs"]
    matrix = idx["matrix"]
    cos = matrix @ _t._embed_query(query)

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
    ranked = _t._rerank_order(query, window, fused, idx, adjacency)[:k]

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
        spec = get_source(d["source"])
        if d.get("repo") is not None:
            card["repo"] = d.get("repo")
        if spec:
            for rel in outbound_relationships(spec.collection):
                card[rel.join.local_field] = d.get(rel.join.local_field) or []
        out.append(card)
    return out
