"""Two-phase action executor — propose, then execute only on explicit approval.

The contract that keeps write-back safe (docs/yoku_agent.md Phase 6):

    propose(...)  validates and writes an `action_log` row, status `proposed`.
                  NOTHING runs. This is all the agent tool may ever do.
    execute(...)  runs ONLY with an explicit approver identity, under the
                  tenant's stored connector credentials; outcome (executed /
                  failed + result) is recorded on the same audit row, then a
                  targeted `sync_one` refreshes the touched doc so the next
                  answer reflects reality.
    reject(...)   records the "no" — rejections are signal too.

Action types are a closed registry below; adding one means adding its
validator + runner here, nothing anywhere else.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime

from yoku.db.mongo import action_log_collection
from yoku.logging import get_logger
from yoku.utils.keys import PR_KEY_RE

log = get_logger("actions")

_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def _require(payload: dict, *keys: str) -> None:
    # str() first: LLM-built payloads can carry non-string values; a missing
    # or blank field must raise ValueError (which callers handle), never
    # AttributeError into the agent loop.
    missing = [k for k in keys if not str(payload.get(k) or "").strip()]
    if missing:
        raise ValueError(f"payload missing required field(s): {missing}")


def _norm_key(key: str, prefix: str) -> str:
    """Strip a canonical 'jira/'-style prefix so targets accept both forms."""
    return key.removeprefix(f"{prefix}/").strip()


# ---------------------------------------------------------------------------
# Runners — each executes ONE action type under bound connector credentials.
# ---------------------------------------------------------------------------


def _run_transition_ticket(target: str, payload: dict) -> dict:
    from yoku.connectors.jira.client import transition_issue

    return transition_issue(_norm_key(target, "jira"), payload["to_status"])


def _run_comment_on_jira(target: str, payload: dict) -> dict:
    from yoku.connectors.jira.client import add_comment

    return add_comment(_norm_key(target, "jira"), payload["body"])


def _run_create_jira_ticket(target: str, payload: dict) -> dict:
    from yoku.connectors.jira.client import create_issue

    return create_issue(
        summary=payload["summary"],
        description=payload.get("description") or "",
        issue_type=payload.get("issue_type") or "Task",
    )


def _run_link_pr_to_ticket(target: str, payload: dict) -> dict:
    """Materialize the cross-source link: comment on the ticket + write the
    local link fields so the agent's answers reflect it immediately."""
    from yoku.connectors.jira.client import add_comment
    from yoku.db.mongo import dc_github_collection, dc_jira_collection

    ticket = _norm_key(target, "jira")
    pr_key = _norm_key(payload["pr_key"], "github")
    comment = add_comment(ticket, f"Linked PR: {pr_key} (via yoku)")
    dc_github_collection().update_one({"key": pr_key}, {"$addToSet": {"jira_keys": ticket}})
    pr = dc_github_collection().find_one({"key": pr_key}) or {}
    dc_jira_collection().update_one(
        {"key": ticket},
        {
            "$pull": {"linked_prs": {"key": pr_key}},
        },
    )
    dc_jira_collection().update_one(
        {"key": ticket},
        {
            "$addToSet": {
                "linked_prs": {
                    "key": pr_key,
                    "repo": pr.get("repo"),
                    "summary": pr.get("summary"),
                    "status": pr.get("status"),
                    "url": pr.get("url"),
                    "merged": pr.get("merged", False),
                }
            }
        },
    )
    return {"ticket": ticket, "pr": pr_key, "comment_id": comment.get("comment_id")}


def _run_comment_on_pr(target: str, payload: dict) -> dict:
    from yoku.connectors.github.client import comment_on_pr

    m = PR_KEY_RE.match(_norm_key(target, "github"))
    if not m:
        raise ValueError(f"target {target!r} is not an org/repo#N PR key")
    return comment_on_pr(m.group(1), int(m.group(2)), payload["body"])


def _validate_jira_target(target: str) -> None:
    if not _JIRA_KEY_RE.match(_norm_key(target, "jira")):
        raise ValueError(f"target {target!r} is not a JIRA key")


def _validate_pr_target(target: str) -> None:
    if not PR_KEY_RE.match(_norm_key(target, "github")):
        raise ValueError(f"target {target!r} is not an org/repo#N PR key")


def _no_target(target: str) -> None:
    """Creation actions have no pre-existing target — nothing to validate."""


#: type -> (connector whose creds the runner needs, target validator,
#:          required payload keys, runner)
ACTION_TYPES: dict[str, dict] = {
    "transition_ticket": {
        "connector": "jira",
        "validate_target": _validate_jira_target,
        "required": ("to_status",),
        "run": _run_transition_ticket,
        "description": "Move a JIRA ticket to a new status.",
    },
    "comment_on_jira": {
        "connector": "jira",
        "validate_target": _validate_jira_target,
        "required": ("body",),
        "run": _run_comment_on_jira,
        "description": "Comment on a JIRA ticket.",
    },
    "create_jira_ticket": {
        "connector": "jira",
        "validate_target": _no_target,
        "required": ("summary",),
        "run": _run_create_jira_ticket,
        "description": "Create a JIRA ticket in the configured project.",
    },
    "link_pr_to_ticket": {
        "connector": "jira",
        "validate_target": _validate_jira_target,
        "required": ("pr_key",),
        "run": _run_link_pr_to_ticket,
        "description": "Link a PR to a JIRA ticket (comment + local link fields).",
    },
    "comment_on_pr": {
        "connector": "github",
        "validate_target": _validate_pr_target,
        "required": ("body",),
        "run": _run_comment_on_pr,
        "description": "Comment on a GitHub pull request.",
    },
}


def propose(
    action_type: str,
    target_key: str,
    payload: dict,
    proposed_by: str,
    source: str,
    signal_id: str | None = None,
) -> dict:
    """Validate and record a proposal. Executes nothing. Returns the audit row."""
    spec = ACTION_TYPES.get(action_type)
    if spec is None:
        raise ValueError(f"unknown action type {action_type!r}; known: {sorted(ACTION_TYPES)}")
    spec["validate_target"](target_key)
    _require(payload, *spec["required"])

    row = {
        "action_id": uuid.uuid4().hex,
        "action_type": action_type,
        "target_key": target_key,
        "payload": payload,
        "status": "proposed",
        "proposed_by": proposed_by,
        "source": source,  # "agent" | "api" | "dm_reply"
        "signal_id": signal_id,
        "proposed_at": datetime.now(UTC),
        "approved_by": None,
        "result": None,
        "error": None,
    }
    action_log_collection().insert_one(row)
    row.pop("_id", None)
    log.info("action proposed %s %s by %s", action_type, target_key, proposed_by)
    return row


def execute(action_id: str, approved_by: str) -> dict:
    """Run a proposed action under the tenant's stored connector credentials.

    Only `proposed` rows run; outcome lands on the audit row either way, and
    a targeted sync_one refreshes the touched doc after success.
    """
    coll = action_log_collection()
    row = coll.find_one({"action_id": action_id})
    if row is None:
        raise ValueError(f"unknown action {action_id!r}")
    if row["status"] != "proposed":
        raise ValueError(f"action {action_id} is {row['status']!r}, not proposed")
    spec = ACTION_TYPES[row["action_type"]]

    t0 = time.monotonic()
    try:
        with _bound_connector(spec["connector"]):
            result = spec["run"](row["target_key"], row["payload"])
        coll.update_one(
            {"action_id": action_id},
            {
                "$set": {
                    "status": "executed",
                    "approved_by": approved_by,
                    "result": result,
                    "executed_at": datetime.now(UTC),
                }
            },
        )
        log.info(
            "action executed %s %s elapsed=%.1fs",
            row["action_type"],
            row["target_key"],
            time.monotonic() - t0,
        )
    except Exception as e:
        coll.update_one(
            {"action_id": action_id},
            {
                "$set": {
                    "status": "failed",
                    "approved_by": approved_by,
                    "error": f"{type(e).__name__}: {e}",
                    "executed_at": datetime.now(UTC),
                }
            },
        )
        log.exception("action failed %s %s", row["action_type"], row["target_key"])
        raise

    _sync_after(row, result)
    return coll.find_one({"action_id": action_id}, {"_id": 0})


def reject(action_id: str, rejected_by: str) -> dict:
    coll = action_log_collection()
    row = coll.find_one({"action_id": action_id})
    if row is None:
        raise ValueError(f"unknown action {action_id!r}")
    if row["status"] != "proposed":
        raise ValueError(f"action {action_id} is {row['status']!r}, not proposed")
    coll.update_one(
        {"action_id": action_id},
        {
            "$set": {
                "status": "rejected",
                "approved_by": rejected_by,
                "rejected_at": datetime.now(UTC),
            }
        },
    )
    return coll.find_one({"action_id": action_id}, {"_id": 0})


def _bound_connector(name: str):
    """Context manager binding the tenant's stored credentials for a runner."""
    from yoku.db import connector_configs as cc

    decrypted = cc.get_config_decrypted(name)
    if not decrypted:
        raise RuntimeError(f"{name} connector is not configured for this tenant")
    if name == "jira":
        from yoku.connectors._runtime import jira_config_from_dict, use_jira

        return use_jira(jira_config_from_dict(decrypted))
    if name == "github":
        from yoku.connectors._runtime import github_config_from_dict, use_github

        return use_github(github_config_from_dict(decrypted))
    if name == "slack":
        from yoku.connectors._runtime import slack_config_from_dict, use_slack

        return use_slack(slack_config_from_dict(decrypted))
    raise RuntimeError(f"no connector binding for {name!r}")


def _sync_after(row: dict, result: dict) -> None:
    """Best-effort targeted refresh so the next answer reflects the change.

    Each target binds ITS OWN connector (a ticket refresh needs JIRA creds, a
    PR refresh needs GitHub creds) — the action's binding has already exited,
    and e.g. link_pr_to_ticket runs under JIRA but must refresh a PR too.
    """
    targets = [row["target_key"]]
    if row["action_type"] == "create_jira_ticket" and result.get("key"):
        targets = [result["key"]]
    if row["action_type"] == "link_pr_to_ticket" and row["payload"].get("pr_key"):
        targets.append(row["payload"]["pr_key"])

    from yoku.pipeline.sync_one import is_pr_key, sync_one

    for t in targets:
        try:
            with _bound_connector("github" if is_pr_key(t) else "jira"):
                sync_one(t)
        except Exception:
            log.exception("post-action sync_one(%s) failed (next scheduled sync will catch up)", t)
