"""Generic mongo escape-hatch tools: list / describe / count / aggregate.

For anything the narrow tools can't express. `mongo_query` runs a bounded,
read-only aggregation pipeline; the validators here enforce the safety envelope
(stage cap, blocked operators, whitelisted cross-collection targets).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from agent_yoku.agent import tools as _t
from agent_yoku.agent.relationships import relationships_for
from agent_yoku.agent.schema_registry import collection_description, field_specs
from agent_yoku.agent.schema_registry import filter_fields as schema_filter_fields
from agent_yoku.agent.sources import source_for_collection

_BLOCKED_STAGES = {"$out", "$merge", "$function", "$accumulator", "$where"}

# Stages that read a second collection: their target is checked against the read
# whitelist so the agent can't $lookup/$unionWith into auth_users or connector_configs.
_CROSS_COLLECTION_STAGES = {"$lookup", "$unionWith", "$graphLookup"}

_MAX_PIPELINE_STAGES = 20
_MAX_INLINE_RESULTS = 100
_HIDDEN_FIELDS = {"embedding", "text"}
_DESCRIBE_DROP_FIELDS = {"_id", "embedding", "text", "description"}

_MAX_FIELD_EXAMPLES = 3
_EXAMPLE_STR_CAP = 60


def _cross_collection_target(op: str, spec: Any) -> str | None:
    """The foreign collection a cross-collection stage reads from, if any."""
    if op == "$unionWith":
        return spec if isinstance(spec, str) else (spec or {}).get("coll")
    if isinstance(spec, dict):  # $lookup / $graphLookup name it in `from`
        return spec.get("from")
    return None


def _validate_stage(stage: Any, i: int) -> None:
    if not isinstance(stage, dict) or len(stage) != 1:
        raise ValueError(f"stage {i} must be a single-key dict {{'$op': {{...}}}}")
    op = next(iter(stage))
    if not op.startswith("$"):
        raise ValueError(f"stage {i} key {op!r} is not a Mongo operator (missing $)")
    if op in _BLOCKED_STAGES:
        raise ValueError(f"stage {i} uses blocked operator {op!r}")
    spec = stage[op]
    if op in _CROSS_COLLECTION_STAGES:
        target = _cross_collection_target(op, spec)
        if not isinstance(target, str) or target not in _t.ALLOWED_COLLECTIONS:
            raise ValueError(
                f"stage {i} {op} reads collection {target!r}, which is not on the read "
                f"whitelist {sorted(_t.ALLOWED_COLLECTIONS)}"
            )
    # Recurse into nested sub-pipelines so they can't smuggle a blocked stage.
    if isinstance(spec, dict):
        nested = spec.get("pipeline")
        if isinstance(nested, list):
            for j, sub in enumerate(nested):
                _validate_stage(sub, j)
    if op == "$facet" and isinstance(spec, dict):
        for sub_pipeline in spec.values():
            if isinstance(sub_pipeline, list):
                for j, sub in enumerate(sub_pipeline):
                    _validate_stage(sub, j)


def _validate_pipeline(pipeline: list[dict]) -> None:
    if not isinstance(pipeline, list) or not pipeline:
        raise ValueError("pipeline must be a non-empty list of stage objects")
    if len(pipeline) > _MAX_PIPELINE_STAGES:
        raise ValueError(f"pipeline has {len(pipeline)} stages; max is {_MAX_PIPELINE_STAGES}")
    for i, stage in enumerate(pipeline):
        _validate_stage(stage, i)


def _collect_example(bucket: list, value: Any) -> None:
    """Record up to a few distinct, compact example values for a field."""
    if value is None or len(bucket) >= _MAX_FIELD_EXAMPLES:
        return
    if isinstance(value, list | dict):
        return  # skip nested containers — keep examples readable
    sample = value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return
        sample = s[:_EXAMPLE_STR_CAP] + ("…" if len(s) > _EXAMPLE_STR_CAP else "")
    if sample not in bucket:
        bucket.append(sample)


def _indexed_fields(coll: Any) -> list[str]:
    """Top-level indexed fields — cheap to $match / sort on. Best-effort."""
    fields: list[str] = []
    try:
        for spec in coll.index_information().values():
            for field_name, _direction in spec.get("key", []):
                if field_name != "_id" and field_name not in fields:
                    fields.append(field_name)
    except Exception:
        return []
    return fields


def _relationship_summaries(collection: str) -> list[dict]:
    """Relationships a collection participates in, for discovery."""
    out = []
    for r in relationships_for(collection):
        other = r.entity2 if r.entity1 == collection else r.entity1
        out.append({"with": other, "type": r.relationship_type, "via": r.join.local_field})
    return out


@tool
def list_collections() -> list[dict] | dict:
    """Enumerate the mongo collections the agent may read from.

    For each collection: name, document count, a description (from its schema),
    indexed fields (cheap to $match / sort on), and the relationships it
    participates in. Collections backed by a connector source also report the
    `source` name, an example `key`, and the fields you can pass to
    `filter(source, …)`. This is the live source of truth — call it to discover
    which sources, collections, and links exist; don't assume a fixed set.
    """
    try:
        out = []
        for name, factory in _t.ALLOWED_COLLECTIONS.items():
            coll = factory()
            spec = source_for_collection(name)
            entry: dict[str, Any] = {
                "name": name,
                "count": coll.estimated_document_count(),
                "description": collection_description(name),
                "indexed_fields": _indexed_fields(coll),
                "relationships": _relationship_summaries(name),
            }
            if spec:
                entry["source"] = spec.name
                entry["key_example"] = spec.key_example
                entry["filter_fields"] = {f.arg: f.help for f in schema_filter_fields(name)}
            out.append(entry)
        return out
    except Exception as e:
        return {"error": f"list_collections failed: {e}"}


def _sample_examples(coll: Any, n: int, field_names: set[str]) -> dict[str, list]:
    """A few distinct live example values per field, from a random sample."""
    examples: dict[str, list] = {}
    for doc in coll.aggregate([{"$sample": {"size": n}}]):
        for k, v in doc.items():
            if k in field_names:
                _collect_example(examples.setdefault(k, []), v)
    return examples


def _describe_by_sampling(coll: Any, collection: str, n: int) -> dict:
    """Fallback for collections with no model — infer fields from a sample."""
    fields: dict[str, dict] = {}
    sampled = 0
    for doc in coll.aggregate([{"$sample": {"size": n}}]):
        sampled += 1
        for k, v in doc.items():
            if k in _DESCRIBE_DROP_FIELDS:
                continue
            entry = fields.setdefault(k, {"types": set(), "count": 0, "examples": []})
            entry["types"].add("null" if v is None else type(v).__name__)
            entry["count"] += 1
            _collect_example(entry["examples"], v)
    out_fields = {
        k: {
            "types": sorted(v["types"]),
            "coverage": round(v["count"] / sampled, 2) if sampled else 0.0,
            "examples": v["examples"],
        }
        for k, v in sorted(fields.items())
    }
    return {"collection": collection, "sampled": sampled, "fields": out_fields}


@tool
def describe_collection(collection: str, sample_size: int = 20) -> dict:
    """Report a collection's fields from its schema — name, type, and description
    — plus a few live example values per field. Use before composing a
    `mongo_query` to reference fields that actually exist and learn enum-like
    values (e.g. status).

    Args:
        collection: name from list_collections() (e.g. "jira_tickets",
            "github_prs", "unified_users").
        sample_size: how many docs to sample for example values (default 20, max 100).

    Returns:
        {"collection", "description", "fields": {field: {"type", "description",
         "display", "filterable", "examples": [...]}}} or {"error": str}.
    """
    try:
        coll = _t.get_collection(collection)
    except ValueError as e:
        return {"error": str(e), "allowed": sorted(_t.ALLOWED_COLLECTIONS)}
    n = max(1, min(int(sample_size), 100))

    specs = field_specs(collection)
    if not specs:
        return _describe_by_sampling(coll, collection, n)

    examples = _sample_examples(coll, n, {s.name for s in specs})
    fields = {
        s.name: {
            "type": s.type,
            "description": s.description,
            "display": s.display,
            "filterable": s.filterable,
            "examples": examples.get(s.name, []),
        }
        for s in specs
    }
    return {
        "collection": collection,
        "description": collection_description(collection),
        "fields": fields,
    }


@tool
def mongo_count(collection: str, filter: dict | None = None) -> dict:
    """Count documents in a collection matching an optional filter.

    Args:
        collection: name from list_collections() (e.g. "jira_tickets",
            "github_prs", "unified_users").
        filter: standard MongoDB find filter, e.g. {"status": "merged",
            "jira_keys": {"$ne": []}}. Omit or pass {} for a fast estimated
            count of the whole collection.

    Returns:
        {"count": int, "estimated": bool} or {"error": str}.
    """
    try:
        coll = _t.get_collection(collection)
    except ValueError as e:
        return {"error": str(e), "allowed": sorted(_t.ALLOWED_COLLECTIONS)}
    try:
        if not filter:
            return {"count": coll.estimated_document_count(), "estimated": True}
        return {"count": coll.count_documents(filter), "estimated": False}
    except Exception as e:
        return {"error": f"mongo_count failed: {e}"}


@tool
def mongo_query(
    collection: str,
    pipeline: list[dict],
    limit: int = 100,
) -> dict:
    """Run a read-only aggregation pipeline on one collection.

    Use this for ad-hoc queries the narrow tools (filter / mongo_count) don't
    cover — e.g. grouping, joins via $lookup, custom projections, or filters on
    fields like fix_versions / labels / nested jira_keys.

    Safety bounds (enforced server-side):
    - pipeline must be <= 20 stages, each a single-key {"$op": {...}} dict
    - blocked stages: $out, $merge, $function, $accumulator, $where
    - $lookup / $unionWith / $graphLookup may only target whitelisted collections
    - results capped at min(limit, 100); $limit auto-appended if absent
    - fields `embedding` and `text` are stripped from results to save tokens

    Pipeline shape: a list of single-key stage objects.
        GOOD: [{"$match": {"repo": "AsatoCorp/asato-svc"}},
               {"$group": {"_id": "$status", "n": {"$sum": 1}}}]
        BAD:  [{"$match": {...}, "fields": {...}}]  ← extra sibling key

    Args:
        collection: collection name from list_collections() (e.g. "jira_tickets",
            "github_prs", "unified_users").
        pipeline: list of mongo aggregation stages.
        limit: max docs to return (default 100, max 100).

    Returns:
        {"results": list[dict], "count": int, "pipeline": list[dict]}
        or {"error": str} on failure.
    """
    try:
        coll = _t.get_collection(collection)
    except ValueError as e:
        return {"error": str(e), "allowed": sorted(_t.ALLOWED_COLLECTIONS)}
    try:
        _validate_pipeline(pipeline)
    except ValueError as e:
        return {"error": f"invalid pipeline: {e}"}

    capped_limit = max(1, min(int(limit), _MAX_INLINE_RESULTS))

    capped_stages: list[dict] = []
    has_limit = False
    for stage in pipeline:
        if "$limit" in stage:
            capped_stages.append({"$limit": min(int(stage["$limit"]), capped_limit)})
            has_limit = True
        else:
            capped_stages.append(stage)
    if not has_limit:
        capped_stages = [*capped_stages, {"$limit": capped_limit}]
    pipeline = capped_stages

    try:
        raw = list(coll.aggregate(pipeline))
    except Exception as e:
        return {"error": f"aggregation failed: {e}", "pipeline": pipeline}

    cleaned = []
    for d in raw:
        for k in _HIDDEN_FIELDS:
            d.pop(k, None)
        cleaned.append(_t.bson_safe(d))
    return {"results": cleaned, "count": len(cleaned), "pipeline": pipeline}
