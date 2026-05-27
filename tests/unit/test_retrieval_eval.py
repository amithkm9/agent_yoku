"""Unit tests for the retrieval eval harness scoring (pure, no index needed)."""

from __future__ import annotations

import pytest

from agent_yoku.eval.retrieval import EvalCase, evaluate


def _fake_search(ranked_by_query):
    """Build a search_fn that returns canned cards (list of keys) per query."""

    def search_fn(query, k, source):
        return [{"key": key} for key in ranked_by_query.get(query, [])[:k]]

    return search_fn


@pytest.mark.unit
def test_perfect_hit_at_rank_one():
    cases = [EvalCase(query="AS-1", expected_keys=("AS-1",))]
    report = evaluate(cases, _fake_search({"AS-1": ["AS-1", "AS-2", "AS-3"]}))

    assert report.hit_rate == 1.0
    assert report.mean_recall == 1.0
    assert report.mrr == 1.0
    assert report.results[0].hit_rank == 1


@pytest.mark.unit
def test_hit_at_rank_three_lowers_mrr():
    cases = [EvalCase(query="AS-1", expected_keys=("AS-1",))]
    report = evaluate(cases, _fake_search({"AS-1": ["AS-9", "AS-8", "AS-1"]}))

    assert report.hit_rate == 1.0
    assert report.results[0].hit_rank == 3
    assert report.mrr == pytest.approx(1 / 3)


@pytest.mark.unit
def test_miss_counts_as_zero():
    cases = [EvalCase(query="AS-1", expected_keys=("AS-1",))]
    report = evaluate(cases, _fake_search({"AS-1": ["AS-2", "AS-3"]}))

    assert report.hit_rate == 0.0
    assert report.mrr == 0.0
    assert report.results[0].hit_rank is None
    assert report.results[0].recall == 0.0


@pytest.mark.unit
def test_partial_recall_over_multiple_expected():
    cases = [EvalCase(query="okta", expected_keys=("A", "B", "C", "D"), k=10)]
    report = evaluate(cases, _fake_search({"okta": ["A", "X", "B", "Y"]}))

    assert report.results[0].recall == 0.5  # 2 of 4 expected present
    assert report.results[0].hit_rank == 1


@pytest.mark.unit
def test_k_truncation_can_drop_expected():
    # Expected key sits at rank 3 but k=2, so it's outside the window -> miss.
    cases = [EvalCase(query="AS-1", expected_keys=("AS-1",), k=2)]
    report = evaluate(cases, _fake_search({"AS-1": ["AS-9", "AS-8", "AS-1"]}))

    assert report.hit_rate == 0.0


@pytest.mark.unit
def test_empty_report_is_zero_not_crash():
    report = evaluate([], _fake_search({}))
    assert report.n == 0
    assert report.hit_rate == 0.0
    assert report.mrr == 0.0
