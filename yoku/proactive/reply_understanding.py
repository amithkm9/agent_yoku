"""Reply understanding — turning a person's words into memory (Phase B).

Phase A captured replies verbatim. This phase reads them. When someone answers
a nudge on Slack, an LLM tier classifies what they meant and yoku acts on it:

    acknowledged       saw it, nothing committed                → note only
    will_fix           intends to fix it (maybe by a date)      → track commitment
    promises           an explicit dated promise                → track commitment
    disputes_gap       "this one isn't actually a gap"          → suppress THIS gap
    explains_as_normal "this is just how we/I work"             → suppress + learn
    question           asks yoku something                      → note only (needs human)
    other              none of the above                        → note only

The two suppression outcomes close the learning loop that was broken before:
a reply that explains a gap (1) settles this signal so it stops nagging and
(2) feeds person memory so future occurrences are cheaper to dismiss — the same
machinery a human Inbox dismissal uses. A reply is landed in the sticky
`dismissed` state (resolution `explained`/`disputed`) precisely because the
detector reopens merely `resolved` signals while they're still observed.

Mirrors the judge: a budgeted queue (`episodes` lacking `understanding`), an
injectable `LlmFn` so tests never hit OpenAI, deferrals logged not silent.
Commitment *follow-ups* (nudging when a promise lapses) are Phase D; this phase
only records the promise. Wiring learned patterns into the judge's person
dimension is Phase C; here they land in person memory.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime

from yoku.config import settings
from yoku.db.mongo import conversations_collection, signals_collection
from yoku.logging import get_logger
from yoku.proactive.episodes import replies_needing_understanding, set_understanding
from yoku.proactive.memory import record_dismissal, record_pattern

log = get_logger("reply_understanding")

# Valid outcomes; anything else from the model is coerced to "other".
OUTCOMES = frozenset(
    {
        "acknowledged",
        "will_fix",
        "promises",
        "disputes_gap",
        "explains_as_normal",
        "question",
        "other",
    }
)

# prompt -> parsed JSON. Injectable so tests never reach OpenAI.
LlmFn = Callable[[str], dict]

_PROMPT = """A teammate-bot (yoku) noticed a possible tracking gap and sent this person \
a short Slack nudge. Read their reply and classify what it means.

Gap type: {detector}
Item: {item_key}
yoku said: {nudge}
Their reply: {reply}

Pick exactly one outcome:
- acknowledged: they saw it but committed to nothing specific.
- will_fix: they intend to fix it (a date may or may not be given).
- promises: an explicit promise, usually with a time ("by tomorrow", "this week").
- disputes_gap: they say this specific item is not actually a gap.
- explains_as_normal: they explain this is simply how they or the team work \
(e.g. "we don't ticket spikes", "design tasks never have PRs") — a general rule, \
not just about this one item.
- question: they ask yoku something / need a human.
- other: none of the above.

If they explain a GENERAL workflow rule, capture it in `learned_pattern` as one \
short sentence (else null). If they promise a fix, capture `commitment` with the \
quoted intent and a due date in YYYY-MM-DD if one is stated or clearly implied \
(else due null). Today is {today}.

Respond with JSON only:
{{"outcome": "<one of the above>",
  "learned_pattern": "<sentence or null>",
  "commitment": {{"text": "<what they promised>", "due": "<YYYY-MM-DD or null>"}} or null,
  "reason": "<one short sentence>"}}"""


def _openai_understand(prompt: str) -> dict:
    """Default LLM call — JSON-mode, pinned prompt above."""
    from yoku.config import openai_client

    resp = openai_client().chat.completions.create(
        model=settings.openai_chat_model,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return json.loads(resp.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        log.warning("reply understanding: non-JSON verdict; treating as 'other'")
        return {}


def _nudge_text(signal_id: str | None) -> str:
    if not signal_id:
        return "(unknown)"
    convo = conversations_collection().find_one({"signal_id": signal_id}, {"message": 1, "_id": 0})
    return (convo or {}).get("message") or "(unknown)"


def _normalize(raw: dict) -> dict:
    """Coerce the model's output into the stored shape."""
    outcome = raw.get("outcome")
    if outcome not in OUTCOMES:
        outcome = "other"
    commitment = raw.get("commitment")
    if not isinstance(commitment, dict) or not commitment.get("text"):
        commitment = None
    pattern = raw.get("learned_pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        pattern = None
    return {
        "outcome": outcome,
        "learned_pattern": pattern.strip() if pattern else None,
        "commitment": commitment,
        "reason": raw.get("reason"),
    }


def _apply_effects(episode: dict, understanding: dict, now: datetime) -> None:
    """Act on a parsed reply: suppress + learn, or record a commitment.

    Only ever touches a still-live signal — a human verdict or a self-heal that
    already settled it wins.
    """
    signal_id = episode.get("signal_id")
    if not signal_id:
        return
    coll = signals_collection()
    sig = coll.find_one({"signal_id": signal_id}, {"_id": 0, "status": 1})
    if not sig or sig.get("status") not in ("open", "shadow", "sent"):
        return

    outcome = understanding["outcome"]
    person = episode.get("person_user_id")
    detector = episode.get("detector")
    item_key = episode.get("item_key")
    note = understanding.get("learned_pattern")

    if outcome in ("disputes_gap", "explains_as_normal"):
        resolution = "explained" if outcome == "explains_as_normal" else "disputed"
        coll.update_one(
            {"signal_id": signal_id},
            {
                "$set": {
                    "status": "dismissed",  # sticky — the detector won't reopen it
                    "resolution": resolution,
                    "resolved_at": now,
                    "verdict": {
                        "real": False,
                        "suppressed_by": "reply",
                        "reason": note or understanding.get("reason"),
                        "judged_at": now,
                    },
                }
            },
        )
        if person:
            # A general "this is normal" feeds the judge's existing dismissal
            # counter so future occurrences for this person suppress cheaply.
            if outcome == "explains_as_normal" and detector:
                record_dismissal(
                    user_id=person, pattern=detector, item_key=item_key or "", by="reply", note=note
                )
            if note:
                record_pattern(person, note=note, by="reply", pattern=detector)

    elif outcome in ("will_fix", "promises"):
        commitment = understanding.get("commitment")
        if commitment:
            coll.update_one(
                {"signal_id": signal_id},
                {"$set": {"commitment": {**commitment, "made_at": now, "source": "reply"}}},
            )


def understand_replies(
    now: datetime | None = None,
    llm: LlmFn | None = None,
    budget: int | None = None,
) -> dict[str, int]:
    """Parse un-understood reply episodes, enrich them, and act.

    Returns counters: candidates / understood / errors (and disabled=True when
    the feature flag is off).
    """
    now = now or datetime.now(UTC)
    if not settings.reply_understanding_enabled:
        return {"candidates": 0, "understood": 0, "errors": 0, "disabled": True}

    llm = llm or _openai_understand
    budget = settings.reply_understanding_budget_per_run if budget is None else budget
    t0 = time.monotonic()
    candidates = replies_needing_understanding(limit=max(budget, 0))
    counts = {"candidates": len(candidates), "understood": 0, "errors": 0}

    for ep in candidates:
        try:
            prompt = _PROMPT.format(
                detector=ep.get("detector") or "unknown",
                item_key=ep.get("item_key") or "unknown",
                nudge=_nudge_text(ep.get("signal_id")),
                reply=ep.get("text") or "",
                today=now.date().isoformat(),
            )
            understanding = _normalize(llm(prompt))
        except Exception:
            log.exception("reply understanding failed for episode %s", ep.get("episode_id"))
            counts["errors"] += 1
            continue
        set_understanding(ep["episode_id"], {**understanding, "understood_at": now})
        _apply_effects(ep, understanding, now)
        counts["understood"] += 1

    log.info("reply understanding done %s elapsed=%.1fs", counts, time.monotonic() - t0)
    return counts
