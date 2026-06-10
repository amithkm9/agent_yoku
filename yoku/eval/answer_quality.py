"""Score final agent answers against a golden set — the end-to-end regression net.

`eval/retrieval.py` scores retrieval; nothing scored the *answer* until now.
Each golden case pins what a correct answer must cite (`expected_citations`)
and say (`must_mention`), plus an optional LLM judge for groundedness.

The harness is pure: it takes an `ask_fn` (query -> final answer text) and an
optional `judge_fn`, so unit tests drive it with fakes and
`scripts/answer_eval.py` wires it to the live agent. Run it on prompt, tool,
or model changes — `make eval`.

Judge stability: the rubric prompt below is pinned (gpt-5.x models only run
at default temperature, so the prompt is the stability lever); change it
deliberately, never casually, or scores shift under your feet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

# Same citation shapes the agent is prompted to emit and the UI linkifies.
_CITATION_RE = re.compile(r"\bAS-\d+\b|\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+\b")

# (query) -> final answer text.
AskFn = Callable[[str], str]
# (query, answer) -> {"grounded": bool, "issues": str}.
JudgeFn = Callable[[str, str], dict]


@dataclass(frozen=True)
class AnswerCase:
    query: str
    expected_citations: tuple[str, ...] = ()
    must_mention: tuple[str, ...] = ()
    category: str = "point"  # point | cross-source | person | broad | analytical
    note: str = ""


@dataclass(frozen=True)
class AnswerResult:
    case: AnswerCase
    answer: str
    cited: tuple[str, ...]  # expected citations actually present
    mentioned: tuple[str, ...]  # must_mention strings actually present
    judge: dict | None = None

    @property
    def citation_recall(self) -> float:
        if not self.case.expected_citations:
            return 1.0
        return len(self.cited) / len(self.case.expected_citations)

    @property
    def mentions_ok(self) -> bool:
        return len(self.mentioned) == len(self.case.must_mention)

    @property
    def grounded(self) -> bool | None:
        return None if self.judge is None else bool(self.judge.get("grounded"))

    @property
    def passed(self) -> bool:
        """A case passes when every expected citation and mention is present
        and the judge (when run) found no ungrounded claims."""
        return self.citation_recall == 1.0 and self.mentions_ok and self.grounded is not False


@dataclass(frozen=True)
class AnswerReport:
    results: tuple[AnswerResult, ...] = ()

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return sum(1 for r in self.results if r.passed) / self.n if self.results else 0.0

    @property
    def mean_citation_recall(self) -> float:
        return sum(r.citation_recall for r in self.results) / self.n if self.results else 0.0

    @property
    def mention_rate(self) -> float:
        return sum(1 for r in self.results if r.mentions_ok) / self.n if self.results else 0.0

    @property
    def grounded_rate(self) -> float | None:
        judged = [r for r in self.results if r.grounded is not None]
        if not judged:
            return None
        return sum(1 for r in judged if r.grounded) / len(judged)


def load_cases(path) -> list[AnswerCase]:
    """Load golden cases from a YAML file: a top-level `cases:` list."""
    import yaml

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cases: list[AnswerCase] = []
    for c in raw.get("cases") or []:
        cases.append(
            AnswerCase(
                query=c["query"],
                expected_citations=tuple(c.get("expected_citations") or []),
                must_mention=tuple(c.get("must_mention") or []),
                category=c.get("category", "point"),
                note=c.get("note", ""),
            )
        )
    return cases


def evaluate_answers(
    cases: list[AnswerCase],
    ask_fn: AskFn,
    judge_fn: JudgeFn | None = None,
) -> AnswerReport:
    """Run each case through the agent and score citations, mentions, grounding."""
    results: list[AnswerResult] = []
    for case in cases:
        answer = ask_fn(case.query)
        found = set(_CITATION_RE.findall(answer))
        cited = tuple(k for k in case.expected_citations if k in found)
        lowered = answer.lower()
        mentioned = tuple(m for m in case.must_mention if m.lower() in lowered)
        judge = judge_fn(case.query, answer) if judge_fn else None
        results.append(AnswerResult(case, answer, cited, mentioned, judge))
    return AnswerReport(tuple(results))


_JUDGE_PROMPT = """You are grading an AI assistant's answer about a team's \
JIRA tickets, GitHub PRs, and Slack discussions.

Question: {query}

Answer to grade:
{answer}

Grade ONLY groundedness. An answer is GROUNDED when every item it makes \
claims about is identified by its source key (like AS-1234 or org/repo#56) \
SOMEWHERE in the answer — one key per item anywhere is enough; do NOT demand \
a citation on each individual sentence, status, date, or detail about an \
already-cited item. Mark UNGROUNDED only for: claims about an item that is \
never identified by any key, clearly invented specifics with no plausible \
source, or self-contradiction. Aggregate counts and statistics ("42 tickets \
are Done") are grounded without per-item keys. Hedges and "no results found" \
are grounded. Respond with JSON only:
{{"grounded": true/false, "issues": "<one short sentence; empty if grounded>"}}"""


def llm_judge(query: str, answer: str) -> dict:
    """Pinned-rubric groundedness judge. Returns {"grounded": bool, "issues": str}."""
    from yoku.config import openai_client, settings

    resp = openai_client().chat.completions.create(
        model=settings.openai_chat_model,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": _JUDGE_PROMPT.format(query=query, answer=answer)}],
    )
    try:
        verdict = json.loads(resp.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return {"grounded": True, "issues": "judge returned non-JSON; skipped"}
    return {"grounded": bool(verdict.get("grounded", True)), "issues": verdict.get("issues", "")}
