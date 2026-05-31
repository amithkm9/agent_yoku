"""`filter` — exact field criteria over one source (no semantic search)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.tools import tool

from yoku.agent import tools as _t
from yoku.agent.schema_registry import HAS_REFS, USER, FilterField
from yoku.agent.schema_registry import filter_fields as schema_filter_fields
from yoku.agent.schema_registry import projection as schema_projection
from yoku.agent.sources import get_source, source_names


def _since(days: int | None) -> datetime | None:
    if not days:
        return None
    return datetime.now(UTC) - timedelta(days=int(days))


def _filter_clause(fld: FilterField, value: Any) -> dict:
    """Translate one {field: value} criterion into a mongo find clause."""
    if fld.kind == HAS_REFS:
        if bool(value):
            return {fld.mongo_field: {"$ne": []}}
        return {fld.mongo_field: {"$in": [[], None]}}
    if fld.kind == USER:
        return {fld.mongo_field: _t._identity(str(value), fld.identity)}
    v = fld.normalize(value) if fld.normalize and isinstance(value, str) else value
    return {fld.mongo_field: v}


@tool
def filter(
    source: str,
    filters: dict[str, Any] | None = None,
    since_days: int | None = None,
    limit: int = 20,
) -> list[dict] | dict:
    """Filter one source's items by exact field criteria (no semantic search).

    Args:
        source: which source to filter — a name from list_collections()
            (e.g. "jira", "github", "slack").
        filters: {field: value} exact-match criteria. Valid fields vary by
            source — list_collections() reports each source's filterable fields.
            Person-valued fields (assignee, author) auto-resolve via
            unified_users, so a GitHub login, JIRA name, or email all match.
            Examples:
              jira:   {"status": "In Progress", "assignee": "Akshay Reddy"}
              github: {"repo": "agent-svc", "status": "merged"}
                      {"has_jira_link": true}
              slack:  {"channel": "engineering"}
        since_days: only items updated within the last N days.
        limit: max results, default 20.

    On an unknown source or field, returns {"error": …, "valid_*": [...]} so you
    can correct the call rather than failing the turn.
    """
    spec = get_source(source)
    if not spec:
        return {"error": f"unknown source {source!r}", "valid_sources": source_names()}

    fields = {f.arg: f for f in schema_filter_fields(spec.collection)}
    q: dict = {}
    for arg, value in (filters or {}).items():
        if value is None:
            continue
        fld = fields.get(arg)
        if not fld:
            return {
                "error": f"source {source!r} has no filter field {arg!r}",
                "valid_fields": list(fields),
            }
        q.update(_filter_clause(fld, value))

    since = _since(since_days)
    if since:
        q[spec.sort_field] = {"$gte": since.isoformat()}

    cursor = (
        _t.get_collection(spec.collection)
        .find(q, schema_projection(spec.collection))
        .sort(spec.sort_field, -1)
        .limit(int(limit))
    )
    return [_t._clean(d) for d in cursor]
