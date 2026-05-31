"""Replay golden retrieval queries against the live index and score them.

Builds its golden set from the tenant's own data so it can't go stale: every
embedded item should be findable by its exact key, and a busy repo's PRs should
surface for the repo name. Fails (exit 1) if hit-rate drops below the threshold,
so retrieval changes (fusion weights, reranker, stopwords) can't quietly regress.

Usage:
    python scripts/retrieval_eval.py --tenant asato
    python scripts/retrieval_eval.py --tenant asato --n 30 --k 10 --min-hit-rate 0.9
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yoku.agent import tools
from yoku.config import github_prs_collection, tickets_collection
from yoku.eval.retrieval import EvalCase, evaluate
from yoku.storage import tenancy


def _sample_keys(collection, n: int) -> list[str]:
    pipeline = [{"$match": {"embedding": {"$ne": None}}}, {"$sample": {"size": n}}]
    return [d["key"] for d in collection().aggregate(pipeline) if d.get("key")]


def build_cases(n: int, k: int) -> list[EvalCase]:
    """Exact-key cases for a random sample of JIRA tickets and GitHub PRs."""
    cases: list[EvalCase] = []
    for key in _sample_keys(tickets_collection, n):
        cases.append(EvalCase(query=key, expected_keys=(key,), source="jira", k=k, note="jira key"))
    for key in _sample_keys(github_prs_collection, n):
        cases.append(EvalCase(query=key, expected_keys=(key,), source="github", k=k, note="pr key"))
    return cases


def _search_fn(query: str, k: int, source: str) -> list[dict]:
    return tools.semantic_search.invoke({"query": query, "k": k, "source": source})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="tenant id to evaluate against")
    ap.add_argument("--n", type=int, default=20, help="sample size per source")
    ap.add_argument("--k", type=int, default=10, help="top-k window per query")
    ap.add_argument("--min-hit-rate", type=float, default=0.9, help="fail below this hit rate")
    args = ap.parse_args()

    tenancy.set_tenant(args.tenant)
    cases = build_cases(args.n, args.k)
    if not cases:
        print("no embedded docs to evaluate — run ingest + embed first")
        return 1

    report = evaluate(cases, _search_fn)
    misses = [r for r in report.results if r.hit_rank is None]

    print(f"retrieval eval — {report.n} cases (k={args.k})")
    print(f"  hit_rate    {report.hit_rate:.3f}")
    print(f"  mean_recall {report.mean_recall:.3f}")
    print(f"  mrr         {report.mrr:.3f}")
    for r in misses:
        print(f"  MISS [{r.case.note}] {r.case.query!r} not in top {r.case.k}")

    ok = report.hit_rate >= args.min_hit_rate
    print(f"\n{'PASS' if ok else 'FAIL'} (min_hit_rate={args.min_hit_rate})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
