"""Replay a set of queries through a retrieval function and score the results.

The harness is pure: it takes a `search_fn` so unit tests can drive it with a
fake, and `scripts/retrieval_eval.py` wires it to the live `semantic_search`.
This is the regression net for retrieval changes (fusion weights, the reranker,
stopwords) — capture golden queries, replay, and watch recall / MRR move.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# (query, k, source) -> ranked list of result cards, each with a "key".
SearchFn = Callable[[str, int, str], list[dict]]


@dataclass(frozen=True)
class EvalCase:
    query: str
    expected_keys: tuple[str, ...]
    source: str = "both"
    k: int = 10
    note: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    found_keys: tuple[str, ...]
    hit_rank: int | None  # 1-based rank of the first expected key, or None if absent
    recall: float  # fraction of expected keys present in the top k


@dataclass(frozen=True)
class EvalReport:
    results: tuple[CaseResult, ...] = ()

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def hit_rate(self) -> float:
        """Fraction of cases where at least one expected key made the top k."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.hit_rank is not None) / self.n

    @property
    def mean_recall(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.recall for r in self.results) / self.n

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank of the first expected key (0 when never found)."""
        if not self.results:
            return 0.0
        return sum(1.0 / r.hit_rank for r in self.results if r.hit_rank) / self.n


def evaluate(cases: list[EvalCase], search_fn: SearchFn) -> EvalReport:
    """Run each case through `search_fn` and score recall / hit-rank."""
    results: list[CaseResult] = []
    for case in cases:
        cards = search_fn(case.query, case.k, case.source)
        keys = [c.get("key") for c in cards]
        expected = set(case.expected_keys)
        found = tuple(k for k in case.expected_keys if k in keys)
        hit_rank = next((i + 1 for i, k in enumerate(keys) if k in expected), None)
        recall = len(found) / len(case.expected_keys) if case.expected_keys else 0.0
        results.append(CaseResult(case, found, hit_rank, recall))
    return EvalReport(tuple(results))
