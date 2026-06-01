"""Schema registry — reads collection details off the Pydantic models.

Every collection maps to one model (`yoku.core.models`). The model is the single
source of truth: its docstring is the collection description and its fields carry
descriptions plus `display` / `filterable` metadata (see `models._fields`). The
agent tools read everything from here, so nothing about a collection's shape is
hardcoded in the tool layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

from yoku.schemas import (
    Conversation,
    EntityLink,
    GitHubPR,
    GitHubUser,
    JiraTicket,
    JiraUser,
    PullRequest,
    SlackMessage,
    SlackUser,
    UnifiedUser,
    WorkItem,
)

# filter kinds (mirror the json_schema_extra `filter_kind` values)
EXACT = "exact"
USER = "user"
HAS_REFS = "has_refs"

#: collection name -> the model that defines its document shape.
COLLECTION_MODELS: dict[str, type[BaseModel]] = {
    # dc-* raw connector collections
    "dc-jira": JiraTicket,
    "dc-jira-users": JiraUser,
    "dc-github": GitHubPR,
    "dc-github-users": GitHubUser,
    "dc-slack": SlackMessage,
    "dc-slack-users": SlackUser,
    # ds-* canonical collections
    "ds-work-item": WorkItem,
    "ds-pull-request": PullRequest,
    "ds-conversation": Conversation,
    "ds-entity-links": EntityLink,
    "ds-unified-users": UnifiedUser,
}


def _gh_repo(value: str) -> str:
    """Accept 'agent-svc' or 'AsatoCorp/agent-svc'; store the full name."""
    return value if "/" in value else f"AsatoCorp/{value}"


#: named value canonicalisers referenced by a field's `normalize` metadata.
_NORMALIZERS: dict[str, Callable[[str], str]] = {"gh_repo": _gh_repo}


@dataclass(frozen=True)
class FilterField:
    """A filterable field, derived from a model field's metadata."""

    arg: str  # name the agent passes in filter(filters={…})
    mongo_field: str
    kind: str  # EXACT | USER | HAS_REFS
    identity: str | None  # USER: dotted path in unified_users
    normalize: Callable[[str], str] | None
    help: str


@dataclass(frozen=True)
class FieldSpec:
    """One field's reported shape, for describe_collection.

    Beyond type/description, it carries the filter semantics the agent needs to
    build a correct query without guessing: whether the field is filterable and
    under what arg, how a value is matched (exact / person-alias / has-refs),
    and the closed set of values it may take (`enum`).
    """

    name: str
    type: str
    description: str
    display: bool
    filterable: bool
    filter_arg: str | None = None
    filter_kind: str | None = None  # EXACT | USER | HAS_REFS (when filterable)
    identity: str | None = None  # USER fields: dotted path in unified_users
    normalize: str | None = None  # named canonicaliser applied before matching
    enum: tuple[str, ...] | None = None  # closed value set, if any


def model_for(collection: str) -> type[BaseModel] | None:
    return COLLECTION_MODELS.get(collection)


def collection_description(collection: str) -> str:
    """First paragraph of the model docstring, whitespace-collapsed."""
    model = COLLECTION_MODELS.get(collection)
    if not model or not model.__doc__:
        return ""
    first = model.__doc__.strip().split("\n\n", 1)[0]
    return " ".join(first.split())


def _extra(field: Any) -> dict[str, Any]:
    return field.json_schema_extra or {}


@lru_cache(maxsize=32)
def _json_types(collection: str) -> dict[str, str]:
    """Compact `field -> type label` map from the model's JSON schema."""
    model = COLLECTION_MODELS.get(collection)
    if not model:
        return {}
    props = model.model_json_schema().get("properties", {})
    return {name: _type_label(prop) for name, prop in props.items()}


def _type_label(prop: dict[str, Any]) -> str:
    if "type" in prop:
        if prop["type"] == "array":
            item = (prop.get("items") or {}).get("type", "object")
            return f"array[{item}]"
        return prop["type"]
    if "anyOf" in prop:
        parts = [p.get("type", "object") for p in prop["anyOf"] if p.get("type") != "null"]
        return " | ".join(dict.fromkeys(parts)) or "object"
    return "object"


def field_specs(collection: str) -> list[FieldSpec]:
    model = COLLECTION_MODELS.get(collection)
    if not model:
        return []
    types = _json_types(collection)
    out: list[FieldSpec] = []
    for name, field in model.model_fields.items():
        extra = _extra(field)
        filterable = bool(extra.get("filterable"))
        enum = extra.get("enum_values")
        out.append(
            FieldSpec(
                name=name,
                type=types.get(name, "object"),
                description=field.description or "",
                display=bool(extra.get("display")),
                filterable=filterable,
                filter_arg=extra.get("filter_arg") if filterable else None,
                filter_kind=extra.get("filter_kind", EXACT) if filterable else None,
                identity=extra.get("identity"),
                normalize=extra.get("normalize"),
                enum=tuple(enum) if enum else None,
            )
        )
    return out


def display_fields(collection: str) -> list[str]:
    """Fields flagged `display` — the lean projection for cards."""
    model = COLLECTION_MODELS.get(collection)
    if not model:
        return []
    return [name for name, f in model.model_fields.items() if _extra(f).get("display")]


def projection(collection: str) -> dict[str, int]:
    """Mongo projection ({field: 1}) for a collection's display fields."""
    return {name: 1 for name in display_fields(collection)}


def filter_fields(collection: str) -> list[FilterField]:
    """Filterable fields, derived from each model field's metadata."""
    model = COLLECTION_MODELS.get(collection)
    if not model:
        return []
    out: list[FilterField] = []
    for name, field in model.model_fields.items():
        extra = _extra(field)
        if not extra.get("filterable"):
            continue
        out.append(
            FilterField(
                arg=extra.get("filter_arg") or name,
                mongo_field=name,
                kind=extra.get("filter_kind", EXACT),
                identity=extra.get("identity"),
                normalize=_NORMALIZERS.get(extra.get("normalize")),
                help=field.description or "",
            )
        )
    return out
