"""Run a fixed suite of queries against the agent and print PASS/FAIL.

This is a regression net for prompt-engineering changes. Each case names tool
calls the agent should make AND a snippet expected in the final answer.
Hard-fails when the answer is missing the expected snippet so any prompt
regression shows up loudly.

Usage:
    python scripts/agent_smoke.py            # run all
    python scripts/agent_smoke.py --case 2   # run one case
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yoku.agent.chat import ask, final_answer
from yoku.db.tenancy import set_tenant

CASES: list[dict] = [
    {
        "name": "point lookup by JIRA key",
        "query": "tell me about AS-4143",
        "expect_tools": {"mongo_query"},
        "expect_in_answer": ["AS-4143"],
    },
    {
        "name": "count tickets by fix_version",
        "query": "how many JIRA tickets target release-05-26-2026?",
        "expect_tools": {"mongo_count", "mongo_query"},
        "tools_mode": "any",
        "expect_in_answer": ["release-05-26-2026"],
    },
    {
        "name": "repos by PR count",
        "query": "top 3 repos by PR count",
        "expect_tools": {"mongo_query"},
        "expect_in_answer": ["asato"],  # repo names contain org slug
    },
    {
        "name": "cross-source link via JIRA key",
        "query": "what PRs reference AS-4143?",
        "expect_tools": {"mongo_query"},
        "expect_in_answer": ["AS-4143"],
    },
    {
        "name": "graceful not-found",
        "query": "tell me about AS-99999",
        "expect_tools": {"mongo_query"},
        "expect_in_answer": ["not", "found", "no"],  # accept various phrasings
        "any_of": True,
    },
]


def _tools_used(messages) -> set[str]:
    names: set[str] = set()
    for m in messages:
        for c in getattr(m, "tool_calls", None) or []:
            names.add(c.get("name", "?"))
    return names


def run_case(case: dict, idx: int) -> tuple[bool, str]:
    t0 = time.monotonic()
    result = ask(case["query"])
    elapsed = time.monotonic() - t0
    answer = final_answer(result)
    tools = _tools_used(result.get("messages", []))

    issues: list[str] = []

    expect_tools = case.get("expect_tools", set())
    if expect_tools:
        mode = case.get("tools_mode", "all")
        if mode == "any":
            if not (tools & expect_tools):
                issues.append(f"none of expected tools {expect_tools} called (got {tools})")
        else:
            missing = expect_tools - tools
            if missing:
                issues.append(f"missing tools: {missing} (got {tools})")

    expect_in = case.get("expect_in_answer", [])
    if case.get("any_of"):
        if not any(s.lower() in answer.lower() for s in expect_in):
            issues.append(f"answer matches none of {expect_in}")
    else:
        for s in expect_in:
            if s.lower() not in answer.lower():
                issues.append(f"answer missing {s!r}")

    ok = not issues
    summary = (
        f"[{'PASS' if ok else 'FAIL'}] case {idx} ({elapsed:.1f}s) — {case['name']}\n"
        f"  tools: {sorted(tools)}\n"
        f"  answer: {answer[:180]}{'…' if len(answer) > 180 else ''}"
    )
    if issues:
        summary += "\n  issues:\n    - " + "\n    - ".join(issues)
    return ok, summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=int, default=None, help="run a single case index (1-based)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--tenant", default="asato", help="tenant id (default: asato)")
    args = ap.parse_args()
    set_tenant(args.tenant)

    targets = (
        [(CASES[args.case - 1], args.case)]
        if args.case
        else list(zip(CASES, range(1, len(CASES) + 1)))
    )
    results = []
    for case, idx in targets:
        ok, summary = run_case(case, idx)
        results.append({"name": case["name"], "ok": ok, "summary": summary})
        if not args.json:
            print(summary)
            print("-" * 80)

    if args.json:
        print(json.dumps(results, indent=2))

    n_pass = sum(1 for r in results if r["ok"])
    print(f"\n{n_pass}/{len(results)} passed")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
