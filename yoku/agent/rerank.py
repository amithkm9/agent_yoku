"""ZeroEntropy second-stage reranker.

A hosted cross-encoder that rescores hybrid-search candidates by reading the
(query, document) pair directly — more precise than first-stage rank fusion. It
sits behind a feature flag + API key: when either is missing, the input is empty,
or the API errors or times out, `rerank` returns None so the caller transparently
falls back to the built-in feature reranker. No model runs in-process — this is a
single HTTP call, so it doesn't pull a local ML stack into the service.
"""

from __future__ import annotations

from functools import lru_cache

from zeroentropy import ZeroEntropy

from yoku.config import settings
from yoku.log import get_logger

log = get_logger("rerank")


@lru_cache(maxsize=1)
def _client() -> ZeroEntropy:
    """Process-wide client; reused so the httpx connection pool stays warm."""
    return ZeroEntropy(
        api_key=settings.zeroentropy_api_key.get_secret_value(),
        timeout=settings.rerank_timeout_s,
        max_retries=1,
    )


def rerank(query: str, documents: list[str]) -> list[tuple[int, float]] | None:
    """Score `documents` against `query`, returning (index, relevance) best-first.

    `index` points into the input `documents` list; `relevance` is 0..1. Returns
    None to tell the caller to keep its existing order — when reranking is
    disabled/unconfigured, there is nothing to rank, or the API call fails. Any
    failure is non-fatal: reranking only refines, it must never break search.
    """
    if not settings.rerank_active or not documents:
        return None
    try:
        resp = _client().models.rerank(
            model=settings.rerank_model,
            query=query,
            documents=documents,
            latency="fast",
        )
    except Exception as e:
        log.warning("rerank failed, falling back to feature reranker: %s", e)
        return None
    log.info("reranked %d docs (tokens=%d)", len(documents), resp.total_tokens)
    return [(r.index, r.relevance_score) for r in resp.results]
