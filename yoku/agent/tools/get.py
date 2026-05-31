"""`get` — point lookup by key, auto-routed to its source by key shape."""

from __future__ import annotations

from langchain_core.tools import ToolException, tool

from yoku.agent import tools as _t
from yoku.agent.sources import source_for_key


@tool
def get(key: str) -> dict:
    """Fetch a single item by key, auto-routed to its source by key shape.

    - JIRA ticket key (e.g. 'AS-4163') → full ticket fields including the
      description text and `linked_prs` (PRs that reference this ticket).
    - GitHub PR key (e.g. 'AsatoCorp/agent-svc#173') → full PR fields including
      body, branch, status, jira_keys, etc.
    - Slack message key (e.g. 'C0AB123/1700000000.123456') → full message.

    Raises ValueError if the key shape is unrecognised or no item matches.
    """
    spec = source_for_key(key)
    if not spec:
        raise ToolException(f"unrecognised key shape: {key!r}")
    doc = _t.get_collection(spec.collection).find_one({"key": key}, {"embedding": 0})
    if not doc:
        raise ToolException(f"{spec.label} {key!r} not found")
    return _t._clean(doc)
