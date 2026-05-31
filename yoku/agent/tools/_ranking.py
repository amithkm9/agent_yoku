"""Pure scoring / fusion helpers for hybrid search.

Side-effect-free relevance maths shared by `semantic_search` and `who_knows`:
tokenisation, idf-weighted keyword scoring, Reciprocal Rank Fusion, the feature
reranker and its lexical / recency / adjacency signals. Nothing here touches
Mongo, OpenAI, or the per-tenant index cache — those stateful seams live in the
`tools` package itself so tests can stub them in one place.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/#._][a-z0-9]+)*")
_SPLIT_RE = re.compile(r"[-/#._]+")
_RRF_K = 60
_KEYWORD_WEIGHT = 2.0
_STOP_DF_RATIO = 0.1
_STOP_DF_MIN = 100

_RERANK_MIN_WINDOW = 30
_W_LEX = 0.05
_W_CORR = 0.02
_W_ADJ = 0.15
_W_RECENCY = 0.03
_RECENCY_HALFLIFE_DAYS = 180.0
_WHO_KNOWS_RECENCY_FLOOR = 0.2


def _to_epoch(value: str | datetime | None) -> float | None:
    """Parse an ISO-8601 timestamp (JIRA/GitHub `updated`) to epoch seconds, or
    None if absent/unparsable — callers treat None as 'age unknown'."""
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return None


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
