"""Generic mongo escape-hatch tools: list / describe / count / aggregate.

For anything the narrow tools can't express. `mongo_query` runs a bounded,
read-only aggregation pipeline; the validators here enforce the safety envelope
(stage cap, blocked operators, whitelisted cross-collection targets).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from yoku.agent import tools as _t
from yoku.agent.schema_registry import collection_description, field_specs
from yoku.agent.schema_registry import filter_fields as schema_filter_fields
from yoku.agent.sources import source_for_collection
from yoku.db.freshness import source_freshness
from yoku.schemas.relationships import relationships_for

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


def _field_links(collection: str) -> dict[str, str]:
    """Map each field that joins out to another collection -> "coll.foreign_field".

    Lets describe_collection tell the agent which fields are usable join keys
    (for a `$lookup`) and exactly what they point at.
    """
    out: dict[str, str] = {}
    for r in relationships_for(collection):
        if r.entity1 == collection:
            out[r.join.local_field] = f"{r.entity2}.{r.join.foreign_field}"
    return out


def _field_entry(spec: Any, links: dict[str, str], examples: dict[str, list]) -> dict:
    """The reported shape of one schema field, including filter semantics."""
    entry: dict[str, Any] = {
        "type": spec.type,
        "description": spec.description,
        "display": spec.display,
        "filterable": spec.filterable,
        "examples": examples.get(spec.name, []),
    }
    if spec.filterable:
        # How to filter this field: the arg name, the match kind, and (for person
        # fields) the identity it resolves to — so the agent filters correctly.
        entry["filter_arg"] = spec.filter_arg
        entry["filter_kind"] = spec.filter_kind
        if spec.identity:
            entry["identity"] = spec.identity
        if spec.normalize:
            entry["normalize"] = spec.normalize
    if spec.enum:
        entry["enum"] = list(spec.enum)
    if spec.name in links:
        entry["links_to"] = links[spec.name]
    return entry


def _freshness_by_source() -> dict[str, dict]:
    """Per-source sync freshness, keyed by source name. Best-effort: a storage
    hiccup must not break listing, so failures degrade to no freshness info."""
    try:
        return {r["source"]: r for r in source_freshness()}
    except Exception:
        return {}


@tool
def list_collections() -> list[dict] | dict:
    """Enumerate the mongo collections the agent may read from.

    For each collection: name, document count, a description (from its schema),
    indexed fields (cheap to $match / sort on), and the relationships it
    participates in. Collections backed by a connector source also report the
    `source` name, an example `key`, its filterable fields, and how fresh the
    data is (`last_synced_at` + `synced_ago`) — check that before concluding a
    source is empty, since count 0 can mean unsynced rather than no such work.
    This is the live source of truth — call it to discover which sources,
    collections, and links exist; don't assume a fixed set.
    """
    try:
        fresh = _freshness_by_source()
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
                row = fresh.get(spec.name)
                if row:
                    entry["last_synced_at"] = row.get("last_synced_at")
                    entry["synced_ago"] = row.get("synced_ago")
                    entry["last_sync_status"] = row.get("last_sync_status")
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
    """Report a collection's schema in the detail needed to build a correct query.

    For each field: type, description, live example values, and — when filterable
    — its `filter_arg`, `filter_kind` (exact / user / has_refs), the `identity` a
    person field resolves to, its `enum` (closed value set, e.g. PR status), and
    `links_to` (the field is a join key into another collection). Top level also
    reports the `source`, an example `key`, and the collection's `relationships`.

    Call this before composing a `mongo_query` so you reference fields that exist,
    match real values, and join on the right keys.

    Args:
        collection: name from list_collections() (e.g. "ds-work-item",
            "ds-pull-request", "ds-unified-users").
        sample_size: how many docs to sample for example values (default 20, max 100).

    Returns:
        {"collection", "description", "source"?, "key_example"?, "relationships",
         "fields": {field: {"type", "description", "display", "filterable",
         "filter_arg"?, "filter_kind"?, "identity"?, "enum"?, "links_to"?,
         "examples": [...]}}} or {"error": str}.
    """
    return _describe_one(collection, sample_size)


def _describe_one(collection: str, sample_size: int) -> dict:
    """Core of describe_collection, callable without the @tool wrapper."""
    try:
        coll = _t.get_collection(collection)
    except ValueError as e:
        return {"error": str(e), "allowed": sorted(_t.ALLOWED_COLLECTIONS)}
    n = max(1, min(int(sample_size), 100))
    specs = field_specs(collection)
    if not specs:
        return _describe_by_sampling(coll, collection, n)
    examples = _sample_examples(coll, n, {s.name for s in specs})
    links = _field_links(collection)
    fields = {s.name: _field_entry(s, links, examples) for s in specs}
    out: dict[str, Any] = {
        "collection": collection,
        "description": collection_description(collection),
        "relationships": _relationship_summaries(collection),
        "fields": fields,
    }
    spec = source_for_collection(collection)
    if spec:
        out["source"] = spec.name
        out["key_example"] = spec.key_example
    return out


@tool
def describe_collections(collections: list[str], sample_size: int = 20) -> dict:
    """Return the schema for one or more collections in a single call.

    Pass a list of collection names; each is described in the same detail as
    `describe_collection`. Call this with all collections you need before
    composing queries — the LLM can batch them here instead of round-tripping
    one at a time.

    Args:
        collections: one or more names from the known collection list
            (e.g. ["jira_tickets", "github_prs"]).
        sample_size: docs sampled per collection for example values (default 20,
            max 100).

    Returns:
        {"results": {collection_name: schema_dict, ...}} where each schema_dict
        matches the shape of describe_collection, or has an "error" key on
        failure for that collection.
    """
    return {"results": {name: _describe_one(name, sample_size) for name in collections}}


@tool
def mongo_count(collection: str, filter: dict | None = None) -> dict:
    """Count documents in a collection matching an optional filter.

    Args:
        collection: name from list_collections() (e.g. "ds-work-item",
            "ds-pull-request", "ds-unified-users").
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

    This is the main way to fetch records: point lookups (`$match` on `key`),
    exact filters, grouping, joins via `$lookup`, custom projections, and filters
    on fields like fix_versions / labels / nested jira_keys. Use `mongo_count`
    first to size the result.

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
        collection: collection name from list_collections() (e.g. "ds-work-item",
            "ds-pull-request", "ds-unified-users").
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
