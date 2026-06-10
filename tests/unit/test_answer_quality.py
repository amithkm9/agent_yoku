"""Answer-quality harness scores citations, mentions, and judge verdicts."""

from __future__ import annotations

import pytest

from yoku.eval.answer_quality import (
    AnswerCase,
    evaluate_answers,
    load_cases,
)

pytestmark = pytest.mark.unit


def test_full_pass_with_citations_and_mentions():
    case = AnswerCase(
        query="which PRs implement AS-1?",
        expected_citations=("AS-1", "Org/repo#7"),
        must_mention=("merged",),
    )
    report = evaluate_answers(
        [case], ask_fn=lambda q: "AS-1 was implemented by Org/repo#7, merged last week."
    )
    r = report.results[0]
    assert r.citation_recall == 1.0
    assert r.mentions_ok
    assert r.passed
    assert report.pass_rate == 1.0


def test_missing_citation_fails_case():
    case = AnswerCase(query="q", expected_citations=("AS-1", "AS-2"))
    report = evaluate_answers([case], ask_fn=lambda q: "Only AS-1 is mentioned here.")
    r = report.results[0]
    assert r.citation_recall == 0.5
    assert not r.passed
    assert report.mean_citation_recall == 0.5


def test_mention_check_is_case_insensitive():
    case = AnswerCase(query="q", must_mention=("Krishna",))
    report = evaluate_answers([case], ask_fn=lambda q: "that work is owned by KRISHNA sagiraju")
    assert report.results[0].mentions_ok


def test_judge_verdict_gates_pass():
    case = AnswerCase(query="q", expected_citations=("AS-1",))
    report = evaluate_answers(
        [case],
        ask_fn=lambda q: "AS-1 is done.",
        judge_fn=lambda q, a: {"grounded": False, "issues": "uncited status claim"},
    )
    r = report.results[0]
    assert r.citation_recall == 1.0
    assert r.grounded is False
    assert not r.passed
    assert report.grounded_rate == 0.0


def test_no_judge_means_grounded_unknown():
    case = AnswerCase(query="q")
    report = evaluate_answers([case], ask_fn=lambda q: "anything")
    assert report.results[0].grounded is None
    assert report.grounded_rate is None
    assert report.results[0].passed  # no expectations -> structural pass


def test_load_cases_roundtrip(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text(
        """
cases:
  - query: "tell me about AS-9"
    expected_citations: ["AS-9"]
    must_mention: ["rate limit"]
    category: point
""",
        encoding="utf-8",
    )
    cases = load_cases(p)
    assert cases == [
        AnswerCase(
            query="tell me about AS-9",
            expected_citations=("AS-9",),
            must_mention=("rate limit",),
            category="point",
        )
    ]
