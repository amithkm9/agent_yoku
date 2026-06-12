"""Outbound Slack voice — yoku's ability to DM a person (Phase 4b).

One narrow capability: resolve a person to their Slack identity through
ds-unified-users (never guess), open a DM, post one message. Phase 5's
orchestrator decides WHEN to speak; this module only knows HOW.

Guardrails baked in:
- No Slack identity on the unified record → refuse (don't fall back to name
  matching at send time; identity comes from M0a's unification or not at all).
- Bots are never DM'd.
- `dry_run=True` returns exactly what would be sent without sending — the
  shadow-mode primitive Phase 5 builds on.

Requires the tenant's Slack bot to have `chat:write` + `im:write` scopes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from yoku.logging import get_logger

log = get_logger("messenger")


def resolve_slack_target(person: str) -> dict:
    """Resolve a free-form person reference to a DM-able Slack identity.

    Returns {"user_id", "slack_user_id", "name"} or {"error": ...} with the
    specific refusal reason — callers must treat any error as SKIP.
    """
    from yoku.agent.tools import _resolve_user_doc

    doc = _resolve_user_doc(person)
    if not doc:
        return {"error": f"no unified user matches {person!r}"}
    slack = doc.get("slack") or {}
    if not slack.get("user_id"):
        return {"error": f"{person!r} has no Slack identity on their unified record"}
    if doc.get("is_bot") or slack.get("is_bot"):
        return {"error": f"{person!r} is a bot — refusing to DM"}
    name = (
        (doc.get("jira") or {}).get("displayName")
        or slack.get("display_name")
        or (doc.get("github") or {}).get("login")
    )
    return {"user_id": doc.get("user_id"), "slack_user_id": slack["user_id"], "name": name}


def send_dm(person: str, text: str, dry_run: bool = True) -> dict:
    """DM `text` to `person` (resolved via unified identity).

    Defaults to dry_run — the caller must explicitly opt into sending. Returns
    {"sent"|"would_send", target, channel?, ts?} or {"error": ...}.
    """
    if not text or not text.strip():
        return {"error": "message text must not be empty"}
    target = resolve_slack_target(person)
    if "error" in target:
        log.info("messenger skip: %s", target["error"])
        return target

    if dry_run:
        return {"would_send": text, "target": target, "dry_run": True}

    from yoku.connectors.slack.client import open_dm, post_message

    channel = open_dm(target["slack_user_id"])
    posted = post_message(channel, text)
    log.info(
        "DM sent to %s (%s) channel=%s ts=%s",
        target["name"],
        target["slack_user_id"],
        posted.get("channel"),
        posted.get("ts"),
    )
    return {
        "sent": text,
        "target": target,
        "channel": posted.get("channel"),
        "ts": posted.get("ts"),
        "at": datetime.now(UTC).isoformat(),
    }
