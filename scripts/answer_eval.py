"""Replay the golden answer set through the live agent and score it.

End-to-end regression net for prompt / tool / model changes — scores the
FINAL ANSWER (citations, required mentions, LLM-judged groundedness), where
`retrieval_eval.py` scores only retrieval. Fails (exit 1) below thresholds so
CI can gate on it.

Usage:
    python scripts/answer_eval.py --tenant asato
    python scripts/answer_eval.py --tenant asato --category cross-source
    python scripts/answer_eval.py --tenant asato --no-judge   # skip LLM judge
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yoku.agent.chat import ask, final_answer
from yoku.db.tenancy import set_tenant
from yoku.eval.answer_quality import evaluate_answers, llm_judge, load_cases

_GOLDEN = Path(__file__).resolve().parent.parent / "yoku" / "eval" / "golden_answers.yaml"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tenant", required=True)
    p.add_argument("--golden", default=str(_GOLDEN), help="golden cases YAML")
    p.add_argument("--category", default=None, help="run only this category")
    p.add_argument("--no-judge", action="store_true", help="skip the LLM groundedness judge")
    p.add_argument("--min-pass-rate", type=float, default=0.7)
    p.add_argument("--min-citation-recall", type=float, default=0.8)
    args = p.parse_args()

    set_tenant(args.tenant)
    cases = load_cases(args.golden)
    if args.category:
        cases = [c for c in cases if c.category == args.category]
    if not cases:
        print("no cases selected")
        return 2

    t0 = time.monotonic()
    report = evaluate_answers(
        cases,
        ask_fn=lambda q: final_answer(ask(q)),
        judge_fn=None if args.no_judge else llm_judge,
    )

    for r in report.results:
        flag = "PASS" if r.passed else "FAIL"
        print(f"[{flag}] ({r.case.category}) {r.case.query}")
        if r.citation_recall < 1.0:
            missing = set(r.case.expected_citations) - set(r.cited)
            print(f"       missing citations: {sorted(missing)}")
        if not r.mentions_ok:
            missing = set(r.case.must_mention) - set(r.mentioned)
            print(f"       missing mentions:  {sorted(missing)}")
        if r.grounded is False:
            print(f"       judge: {r.judge.get('issues', '')}")

    grounded = report.grounded_rate
    print(
        f"\ncases={report.n} pass_rate={report.pass_rate:.2f} "
        f"citation_recall={report.mean_citation_recall:.2f} "
        f"mention_rate={report.mention_rate:.2f} "
        f"grounded_rate={'-' if grounded is None else f'{grounded:.2f}'} "
        f"elapsed={time.monotonic() - t0:.0f}s"
    )

    ok = (
        report.pass_rate >= args.min_pass_rate
        and report.mean_citation_recall >= args.min_citation_recall
    )
    if not ok:
        print(
            f"FAIL: thresholds not met "
            f"(pass_rate>={args.min_pass_rate}, citation_recall>={args.min_citation_recall})"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
