"""`propose_action` — the agent suggests a write-back; humans pull the trigger.

The hard rule of Phase 6: the agent NEVER executes side effects. This tool
only validates and records a proposal in the audit log; it runs after an
admin approves it in the Proactive inbox (or via POST /api/actions/{id}/approve).
"""

from __future__ import annotations

from langchain_core.tools import tool

from yoku.actions.executor import ACTION_TYPES, propose


@tool
def propose_action(action_type: str, target_key: str, payload: dict) -> dict:
    """Propose a write-back action for human approval. Nothing executes now —
    the proposal appears in the Proactive inbox where an admin approves or
    rejects it. Use when the user asks yoku to change something in JIRA,
    GitHub, or Slack (close/transition a ticket, link a PR, comment, create a
    ticket).

    Action types and required payload fields:
    - transition_ticket: target=JIRA key, payload {"to_status": "Done"}
    - comment_on_jira:   target=JIRA key, payload {"body": "..."}
    - create_jira_ticket: target="" ,    payload {"summary": "...",
      "description"?: "...", "issue_type"?: "Task"}
    - link_pr_to_ticket: target=JIRA key, payload {"pr_key": "org/repo#123"}
    - comment_on_pr:     target=org/repo#123, payload {"body": "..."}

    Returns {"proposed": true, "action_id", "action_type", "target_key",
    "note"} or {"error": ...}. Tell the user the action awaits approval in
    the Proactive tab — do not claim it was done.
    """
    try:
        row = propose(
            action_type=action_type,
            target_key=target_key or "",
            payload=payload or {},
            proposed_by="agent",
            source="agent",
        )
    except ValueError as e:
        return {"error": str(e), "known_types": sorted(ACTION_TYPES)}
    return {
        "proposed": True,
        "action_id": row["action_id"],
        "action_type": row["action_type"],
        "target_key": row["target_key"],
        "note": "awaiting human approval in the Proactive inbox — not executed yet",
    }
