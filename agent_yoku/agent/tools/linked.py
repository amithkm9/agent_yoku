"""`linked` — cross-source link hop, auto-routed by key shape."""

from __future__ import annotations

from langchain_core.tools import ToolException, tool

from agent_yoku.agent import tools as _t
from agent_yoku.agent.relationships import (
    Relationship,
    inbound_relationships,
    outbound_relationships,
)
from agent_yoku.agent.schema_registry import projection as schema_projection
from agent_yoku.agent.sources import source_for_collection, source_for_key


def _entity_source_name(collection: str) -> str:
    """Tag for results from a collection — its source name, else the collection."""
    spec = source_for_collection(collection)
    return spec.name if spec else collection


def _outbound_links(rel: Relationship, key: str, limit: int) -> list[dict]:
    """Items `key` references, via `rel` (key belongs to rel.entity1)."""
    doc = _t.get_collection(rel.entity1).find_one({"key": key}, {rel.join.local_field: 1})
    if not doc:
        raise ToolException(f"{key!r} not found")
    ref_keys = doc.get(rel.join.local_field) or []
    if not ref_keys:
        return []
    cursor = (
        _t.get_collection(rel.entity2)
        .find({rel.join.foreign_field: {"$in": ref_keys}}, schema_projection(rel.entity2))
        .limit(int(limit))
    )
    tag = _entity_source_name(rel.entity2)
    return [{**_t._clean(d), "source": tag} for d in cursor]


def _inbound_links(rel: Relationship, key: str, limit: int) -> list[dict]:
    """Items referencing `key`, via `rel` (key belongs to rel.entity2)."""
    cursor = (
        _t.get_collection(rel.entity1)
        .find({rel.join.local_field: key}, schema_projection(rel.entity1))
        .limit(int(limit))
    )
    tag = _entity_source_name(rel.entity1)
    return [{**_t._clean(d), "source": tag} for d in cursor]


@tool
def linked(key: str, limit: int = 20) -> list[dict]:
    """Cross-source link hop, auto-routed by key shape. Each result is tagged
    with its `source`. Links come from the relationship registry.

    - A hub key (e.g. JIRA 'AS-4163') → the PRs and Slack messages that
      reference it (inbound links).
    - A referencing key (e.g. PR 'AsatoCorp/agent-svc#173') → the JIRA tickets
      it points at (outbound links).

    `limit` caps results per related collection.
    """
    spec = source_for_key(key)
    if not spec:
        raise ToolException(f"unrecognised key shape: {key!r}")
    out: list[dict] = []
    for rel in outbound_relationships(spec.collection):
        out += _outbound_links(rel, key, limit)
    for rel in inbound_relationships(spec.collection):
        out += _inbound_links(rel, key, limit)
    return out
