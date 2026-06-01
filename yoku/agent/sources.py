"""Source registry — connector/source routing facts only.

A `SourceSpec` holds what isn't in any document schema: how to recognise a key
(`key_pattern`), an example key, the recency sort field, and whether the source
is in the semantic index. Everything *about the documents* — descriptions,
fields, what's filterable — comes from the collection's model via
`schema_registry`; cross-collection links come from `relationships`.

To onboard a connector: add a model (its schema), a `SourceSpec` here, and any
links to `relationships.yaml`. No tool, prompt, or description edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class SourceSpec:
    """Routing facts for one queryable source (its docs are described by a model)."""

    name: str  # short id, matches the `source` field on indexed docs
    collection: str  # also the schema_registry key
    label: str  # shown in prompts / errors, e.g. "GitHub PR"
    key_pattern: str  # regex that identifies a key belonging to this source
    key_example: str  # human-friendly example key, e.g. "AS-4163"
    sort_field: str = "updated"  # recency field for sort / since_days
    embeddable: bool = True  # appears in the semantic index
    routable: bool = True  # key shape is matched by source_for_key


_JIRA = SourceSpec(
    name="jira",
    collection="jira_tickets",
    label="JIRA ticket",
    key_pattern=r"^[A-Z][A-Z0-9]+-\d+$",  # AS-4163, ENG-12
    key_example="AS-4163",
)
_GITHUB = SourceSpec(
    name="github",
    collection="github_prs",
    label="GitHub PR",
    key_pattern=r"#\d+$",  # org/repo#123
    key_example="AsatoCorp/agent-svc#173",
)
_SLACK = SourceSpec(
    name="slack",
    collection="slack_messages",
    label="Slack message",
    key_pattern=r"^[^#]+/\d+\.\d+$",  # channel_id/ts
    key_example="C0AB123/1700000000.123456",
)

#: The canonical cross-source collection (Way B). Its keys are namespaced
#: `provider/native_key`, which overlaps the per-source key shapes above, so it
#: is registered for discovery/filtering but marked non-routable to keep
#: `source_for_key` unambiguous.
_DOCUMENTS = SourceSpec(
    name="documents",
    collection="documents",
    label="Canonical document",
    key_pattern=r"^[a-z]+/",  # provider/native_key — informational; not routed
    key_example="github/AsatoCorp/agent-svc#173",
    embeddable=False,
    routable=False,
)

#: The registry. Append a SourceSpec here to onboard a new queryable source.
SOURCES: tuple[SourceSpec, ...] = (_JIRA, _GITHUB, _SLACK, _DOCUMENTS)

_BY_NAME: dict[str, SourceSpec] = {s.name: s for s in SOURCES}
_BY_COLLECTION: dict[str, SourceSpec] = {s.collection: s for s in SOURCES}


def source_names() -> list[str]:
    return [s.name for s in SOURCES]


def embeddable_sources() -> list[SourceSpec]:
    return [s for s in SOURCES if s.embeddable]


def get_source(name: str) -> SourceSpec | None:
    return _BY_NAME.get(name)


def source_for_collection(collection: str) -> SourceSpec | None:
    return _BY_COLLECTION.get(collection)


@lru_cache(maxsize=64)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _is_canonical_prefix(key: str) -> bool:
    """True if `key` is a canonical `provider/native_key` (documents) key.

    Such keys overlap the per-source shapes (e.g. `github/owner/repo#1` matches
    GitHub's `#\\d+$`), so they must be excluded from per-source routing.
    """
    head, sep, _ = key.partition("/")
    return bool(sep) and head in _BY_NAME


def source_for_key(key: str) -> SourceSpec | None:
    """Identify which per-source collection a key belongs to by its shape.

    Per-source patterns are mutually exclusive: a '#' marks a PR, a
    'channel/ts' marks a Slack message, an 'AS-123' marks a ticket. Canonical
    `provider/native_key` keys (the `documents` collection) are not routed —
    they overlap the per-source shapes — so they return None here.
    """
    if _is_canonical_prefix(key):
        return None
    for spec in SOURCES:
        if not spec.routable:
            continue
        if _compiled(spec.key_pattern).search(key):
            return spec
    return None
