"""`get_memory` / `update_memory` — the agent's window into person memory.

Person memory is the proactive engine's record of how each person works
(dismissals, learned patterns). The agent reads and writes it through these
tools only — the collection is deliberately off the mongo_query whitelist so
memory access always goes through person resolution and a typed shape.
"""

from __future__ import annotations

from langchain_core.tools import tool

from yoku.agent import tools as _t
from yoku.proactive.memory import get_memory as _get_memory
from yoku.proactive.memory import record_pattern


def _resolve_user_id(person: str) -> tuple[str | None, dict | None]:
    doc = _t._resolve_user_doc(person)
    if not doc or not doc.get("user_id"):
        return None, None
    return doc["user_id"], doc


@tool
def get_memory(person: str) -> dict:
    """What yoku remembers about how a person works: dismissed gap patterns
    and learned workflow notes.

    Consult before reasoning about whether something is unusual for a person —
    a "gap" that matches their remembered workflow is not a gap. Returns
    {person, dismissals: [...], patterns: [...]} (empty lists when nothing is
    remembered) or {"error": ...} if the person can't be resolved.

    Args:
        person: free-form reference — name, email, GitHub login, Slack handle.
    """
    user_id, who = _resolve_user_id(person)
    if not user_id:
        return {"error": f"no user matches {person!r}"}
    mem = _get_memory(user_id) or {}
    return {
        "person": {
            "user_id": user_id,
            "name": (who.get("jira") or {}).get("displayName")
            or (who.get("github") or {}).get("login"),
        },
        "dismissals": mem.get("dismissals") or [],
        "patterns": mem.get("patterns") or [],
    }


@tool
def update_memory(person: str, note: str, pattern: str | None = None) -> dict:
    """Record a lasting observation about how a person works, e.g. "leaves
    tickets open until QA signs off" or "ships docs-only changes without
    tickets".

    Use when a conversation reveals a stable workflow fact worth remembering —
    it suppresses future false-positive nudges about that person. Don't record
    one-off events; only patterns.

    Args:
        person: free-form reference — name, email, GitHub login, Slack handle.
        note: the observation, one sentence.
        pattern: optional gap pattern it explains (e.g. "done_no_pr").
    """
    user_id, _ = _resolve_user_id(person)
    if not user_id:
        return {"error": f"no user matches {person!r}"}
    if not note or not note.strip():
        return {"error": "note must not be empty"}
    record_pattern(user_id, note.strip(), by="agent", pattern=pattern)
    return {"ok": True, "user_id": user_id, "note": note.strip(), "pattern": pattern}
