"""Tiered judgment over matured signals (docs/yoku_agent.md Phase 3).

Cheapest gate first, every tier a chance to stop before spending more:

    memory      person dismissed this pattern repeatedly  → suppress (1 read)
    baseline    pattern is this person's normal workflow  → suppress (1 read)
    LLM x2      item-judgment ∧ person-judgment AND-gate  → keep or reject

The two LLM judgments are independent dimensions over PRE-GATHERED evidence
(the code reads the item lifecycle and the person's baseline; the model only
judges). That keeps the design's "two dimensions must agree" epistemics at a
fraction of the cost of tool-using sub-agents — those arrive in Phase 5 where
conversations genuinely need multi-turn reasoning.

Outcomes land on the signal:
  kept     status stays "open",  verdict.real = true   → Inbox shows it
  rejected status -> "judged",   verdict.real = false  → hidden, but LOGGED:
           this is the recall stream (§7.3) — sampled later to measure what
           the AND-gate wrongly swallowed.

Budgeted: at most `settings.judge_budget_per_run` signals take the LLM path
per run; deferrals are logged, never silent, and drain on subsequent syncs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime

from yoku.config import settings
from yoku.db.mongo import (
    ds_entity_links_collection,
    ds_pull_request_collection,
    ds_work_item_collection,
    signals_collection,
)
from yoku.logging import get_logger
from yoku.proactive.baselines import get_baseline
from yoku.proactive.memory import is_pattern_suppressed

log = get_logger("judge")

# Baseline tier: at this rate (with enough history) the pattern IS the
# person's workflow — suppress without burning an LLM call.
BASELINE_SUPPRESS_RATE = 0.8
BASELINE_MIN_N = 5

#: detector -> the baseline rate field that captures "how often is this
#: pattern normal for this person".
_BASELINE_RATE_FIELD = {
    "done_no_pr": "done_no_pr_rate",
    "merged_no_ticket": "merged_no_ticket_rate",
}
_BASELINE_N_FIELD = {
    "done_no_pr": "done_no_pr_n",
    "merged_no_ticket": "merged_no_ticket_n",
}

# (kind, prompt) -> parsed JSON verdict. Injectable so tests never hit OpenAI.
LlmFn = Callable[[str, str], dict]

_ITEM_PROMPT = """You judge whether a cross-source gap in a team's tracking is REAL \
(worth raising with a human) or EXPLAINABLE (normal, no action needed).

Gap type: {detector}
- done_no_pr: a ticket is in a done-state but no PR ever linked to it. \
EXPLAINABLE when the work plainly needs no code (design, research, process, \
documentation, planning, meetings) or the lifecycle shows the code shipped \
under a linked/related item. REAL when it looks like engineering work whose \
code trail is missing.
- merged_no_ticket: a PR merged with no ticket referenced. EXPLAINABLE for \
chores, dependency bumps, automated/bot output, tiny config fixes. REAL for \
substantive feature/fix work that should have been tracked.

Evidence (gathered from the item and its cross-source links):
{evidence}

Respond with JSON only:
{{"real": true/false, "reason": "<one short sentence>"}}"""

_PERSON_PROMPT = """You judge whether a tracking gap is NORMAL WORKFLOW for this \
specific person, or UNUSUAL for them (a slip worth a nudge).

Gap type: {detector} ({gap_description})

Evidence about the person:
{evidence}

High historical rates of the same pattern mean this is simply how they work \
(e.g. a PM or designer whose tickets never have PRs) — that is NORMAL. A low \
historical rate with a sudden occurrence is UNUSUAL. When evidence is thin, \
lean NORMAL — when in doubt, yoku stays quiet.

Respond with JSON only:
{{"normal_for_person": true/false, "reason": "<one short sentence>"}}"""

_GAP_DESCRIPTIONS = {
    "done_no_pr": "their ticket is done-state with no linked PR",
    "merged_no_ticket": "their PR merged with no ticket referenced",
}


def _openai_judge(kind: str, prompt: str) -> dict:
    """Default LLM judgment call — JSON-mode, pinned prompts above."""
    from yoku.config import openai_client

    resp = openai_client().chat.completions.create(
        model=settings.openai_chat_model,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return json.loads(resp.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        log.warning("judge: non-JSON %s verdict; treating as inconclusive", kind)
        return {}


def _item_evidence(signal: dict) -> dict:
    """The item's cross-source lifecycle, compact enough to judge from."""
    item_key = signal["item_key"]
    doc = (
        ds_work_item_collection().find_one({"key": item_key})
        or ds_pull_request_collection().find_one({"key": item_key})
        or {}
    )
    links = list(
        ds_entity_links_collection()
        .find(
            {"$or": [{"from_key": item_key}, {"to_key": item_key}]},
            {"_id": 0, "from_key": 1, "to_key": 1, "link_type": 1},
        )
        .limit(20)
    )
    body = (doc.get("body") or "")[:400]
    return {
        "key": item_key,
        "title": doc.get("title") or signal.get("title"),
        "type": doc.get("type"),
        "status": doc.get("status"),
        "repo": (signal.get("evidence") or {}).get("repo"),
        "body_excerpt": body,
        "cross_source_links": links,
        "gap_age_days": (signal.get("evidence") or {}).get("gap_age_days"),
    }


def _person_evidence(signal: dict) -> dict:
    baseline = get_baseline(signal.get("person_user_id")) or {}
    baseline.pop("computed_at", None)
    return {
        "person": signal.get("person_name") or "unknown",
        "baseline_90d": baseline or "no history",
    }


def judge_signals(
    now: datetime | None = None,
    llm: LlmFn | None = None,
    budget: int | None = None,
) -> dict[str, int]:
    """Run the tiered judgment over unjudged matured signals.

    Returns counters: suppressed_memory / suppressed_baseline / kept /
    rejected / deferred (budget exhausted) / errors.
    """
    now = now or datetime.now(UTC)
    llm = llm or _openai_judge
    budget = settings.judge_budget_per_run if budget is None else budget
    t0 = time.monotonic()
    coll = signals_collection()

    candidates = list(
        coll.find(
            {"status": "open", "matured_at": {"$ne": None}, "verdict": None},
        ).sort([("confidence", -1), ("matured_at", -1)])
    )

    counts = {
        "candidates": len(candidates),
        "suppressed_memory": 0,
        "suppressed_baseline": 0,
        "kept": 0,
        "rejected": 0,
        "deferred": 0,
        "errors": 0,
    }
    # One person-verdict per (person, detector) per run — signals sharing a
    # person reuse it instead of re-asking.
    person_cache: dict[tuple[str | None, str], dict] = {}
    llm_used = 0

    def _settle(signal: dict, verdict: dict, *, keep: bool) -> None:
        coll.update_one(
            {"_id": signal["_id"]},
            {
                "$set": {
                    "verdict": {**verdict, "judged_at": now},
                    "status": "open" if keep else "judged",
                }
            },
        )

    for s in candidates:
        detector = s["detector"]
        user_id = s.get("person_user_id")

        # Tier 0 — human override: a confirmed label outranks every automated
        # tier. The person clicked "real gap"; never argue with that.
        if s.get("label") == "confirmed":
            _settle(s, {"real": True, "confirmed_by": s.get("labeled_by")}, keep=True)
            counts["kept"] += 1
            continue

        # Tier 1 — memory: repeated dismissals of this pattern for this person.
        if is_pattern_suppressed(user_id, detector):
            _settle(s, {"real": False, "suppressed_by": "memory"}, keep=False)
            counts["suppressed_memory"] += 1
            continue

        # Tier 2 — baseline: the pattern is this person's documented norm.
        baseline = get_baseline(user_id)
        rate_field = _BASELINE_RATE_FIELD.get(detector)
        if baseline and rate_field:
            rate = baseline.get(rate_field)
            n = baseline.get(_BASELINE_N_FIELD[detector]) or 0
            if rate is not None and rate >= BASELINE_SUPPRESS_RATE and n >= BASELINE_MIN_N:
                _settle(
                    s,
                    {
                        "real": False,
                        "suppressed_by": "baseline",
                        "reason": f"{rate:.0%} of their recent items show this pattern (n={n})",
                    },
                    keep=False,
                )
                counts["suppressed_baseline"] += 1
                continue

        # Tier 3 — LLM AND-gate, within budget.
        if not settings.judge_enabled or llm_used >= budget:
            counts["deferred"] += 1
            continue
        try:
            item = llm(
                "item",
                _ITEM_PROMPT.format(detector=detector, evidence=json.dumps(_item_evidence(s))),
            )
            cache_key = (user_id, detector)
            if cache_key in person_cache:
                person = person_cache[cache_key]
            elif user_id is None:
                # No person dimension — the item judgment stands alone.
                person = {"normal_for_person": False, "reason": "no owner attributed"}
            else:
                person = llm(
                    "person",
                    _PERSON_PROMPT.format(
                        detector=detector,
                        gap_description=_GAP_DESCRIPTIONS.get(detector, detector),
                        evidence=json.dumps(_person_evidence(s)),
                    ),
                )
                person_cache[cache_key] = person
            llm_used += 1
        except Exception:
            log.exception("judge: LLM tier failed for %s", s.get("signal_id"))
            counts["errors"] += 1
            continue

        # AND-gate: keep only when the item dimension says real AND the person
        # dimension says it is not their normal workflow.
        keep = bool(item.get("real")) and not bool(person.get("normal_for_person"))
        _settle(
            s,
            {
                "real": keep,
                "item": {"real": item.get("real"), "reason": item.get("reason")},
                "person": {
                    "normal_for_person": person.get("normal_for_person"),
                    "reason": person.get("reason"),
                },
            },
            keep=keep,
        )
        counts["kept" if keep else "rejected"] += 1

    log.info("judge done %s elapsed=%.1fs", counts, time.monotonic() - t0)
    return counts
