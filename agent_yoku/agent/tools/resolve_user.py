"""`resolve_user` — free-form person reference → unified user record."""

from __future__ import annotations

from langchain_core.tools import tool

from agent_yoku.agent import tools as _t


@tool
def resolve_user(query: str) -> dict:
    """Resolve a free-form user reference (name, GitHub login, email, or JIRA
    displayName) into a unified user record with each source's identifiers.

    `filter(source, …)` already auto-resolves person-valued fields, so you rarely
    need this directly — reach for it when you want to inspect a person's
    cross-source identities, or disambiguate a partial name before filtering.

    Returns a dict with: email, jira.displayName, jira.accountId, github.login,
    is_bot, match_source. Raises ValueError if no user matches.
    """
    doc = _t._resolve_user_doc(query)
    if not doc:
        raise ValueError(f"no user matches {query!r}")
    return doc
