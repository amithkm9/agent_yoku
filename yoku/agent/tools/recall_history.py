"""`recall_history` — the reactive agent's window into proactive memory.

The chat agent and the proactive engine share a tenant but not a memory: until
now the agent could answer "who knows X?" from the data, but not "what has yoku
flagged for Priya, and what did she say back?". This tool bridges that — it reads
the episode stream, consolidated beliefs, and open commitments the engine
accumulates, through one typed, person-resolved window (the underlying
collections stay off the mongo_query whitelist, like person memory).
"""

from __future__ import annotations

from langchain_core.tools import tool

from yoku.agent import tools as _t
from yoku.db.mongo import signals_collection
from yoku.proactive.beliefs import get_beliefs
from yoku.proactive.episodes import get_episodes
from yoku.utils.bson import bson_safe


def _resolve(person: str) -> tuple[str | None, dict | None]:
    doc = _t._resolve_user_doc(person)
    if not doc or not doc.get("user_id"):
        return None, None
    return doc["user_id"], doc


@tool
def recall_history(person: str, limit: int = 20) -> dict:
    """The timeline of what yoku has done with a person across the proactive
    engine: nudges yoku sent, the person's replies, follow-ups, and human
    confirm/dismiss verdicts — plus the beliefs yoku has formed about how they
    work and any commitments they still owe.

    Use for questions like "what have we flagged for Priya?", "did she reply to
    yoku?", or "what is X on the hook for?". Complements get_memory (suppression
    rules) with the actual history. Returns {person, episodes, beliefs,
    commitments} (newest episodes first) or {"error": ...} if unresolved.

    Args:
        person: free-form reference — name, email, GitHub login, Slack handle.
        limit: max episodes to return (newest first).
    """
    user_id, who = _resolve(person)
    if not user_id:
        return {"error": f"no user matches {person!r}"}

    episodes = [
        {
            "kind": e.get("kind"),
            "text": e.get("text"),
            "detector": e.get("detector"),
            "item_key": e.get("item_key"),
            "outcome": (e.get("understanding") or {}).get("outcome") or e.get("outcome"),
            "ts": e.get("ts"),
        }
        for e in get_episodes(person_user_id=user_id, limit=limit)
    ]
    beliefs = [
        {
            "pattern": b["pattern"],
            "claim": b["claim"],
            "confidence": b["confidence"],
            "evidence_count": b["evidence_count"],
        }
        for b in get_beliefs(user_id)
    ]
    # Include settled signals too (status surfaced) so the agent can answer both
    # "what is X on the hook for?" and "did they do what they promised?".
    commitments = [
        {
            "item_key": s.get("item_key"),
            "detector": s.get("detector"),
            "status": s.get("status"),
            **{
                k: v
                for k, v in (s.get("commitment") or {}).items()
                if k in ("text", "due", "made_at", "followups")
            },
        }
        for s in signals_collection()
        .find(
            {"person_user_id": user_id, "commitment": {"$ne": None}},
            {"_id": 0, "item_key": 1, "detector": 1, "status": 1, "commitment": 1},
        )
        .limit(25)
    ]
    name = (who.get("jira") or {}).get("displayName") or (who.get("github") or {}).get("login")
    return bson_safe(
        {
            "person": {"user_id": user_id, "name": name},
            "episodes": episodes,
            "beliefs": beliefs,
            "commitments": commitments,
        }
    )
