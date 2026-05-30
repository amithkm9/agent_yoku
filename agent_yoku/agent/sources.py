"""Source registry — single source of truth for the agent's data sources.

Every external connector that lands in mongo (JIRA, GitHub, Slack today; GitLab,
Teams, Linear next) is described here once as a `SourceSpec`. The agent tools
(`get`, `linked`, `filter`, `semantic_search`, `list_collections`) read this
registry instead of hardcoding collection names or per-source branches, so
onboarding a new connector is a single entry here — not edits scattered across
every tool, the prompt, and the index loader.

To add a connector: append one `SourceSpec` to `SOURCES`. Nothing else in the
agent layer should need to change.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

# --- filter field kinds ----------------------------------------------------

#: Plain equality. In a mongo `find` filter this also matches array membership,
#: so it covers scalar fields (status) and array fields (labels) alike.
EXACT = "exact"
#: The value names a person; resolve it through `unified_users` to this source's
#: identity before matching (e.g. a GitHub login typed for a JIRA assignee).
USER = "user"
#: Boolean presence check over the source's `ref_field` (e.g. "has a JIRA link").
HAS_REFS = "has_refs"


@dataclass(frozen=True)
class FilterField:
    """One filterable field exposed by `filter(source=…)`."""

    arg: str  # key the agent passes in `filters`, e.g. "assignee"
    mongo_field: str  # field stored on the doc, e.g. "assignee"
    kind: str = EXACT
    identity: str | None = None  # USER kind: dotted path into unified_users
    normalize: Callable[[str], str] | None = None  # optional value canonicaliser
    help: str = ""


@dataclass(frozen=True)
class SourceSpec:
    """Everything the agent layer needs to know about one data source."""

    name: str  # short id, matches the `source` field on indexed docs
    collection: str
    label: str  # shown in prompts / errors, e.g. "GitHub PR"
    key_pattern: str  # regex that identifies a key belonging to this source
    projection: dict[str, int]  # lean projection for filter() result cards
    sort_field: str  # default sort field (descending)
    filter_fields: tuple[FilterField, ...]
    description: str
    embeddable: bool = True  # appears in the semantic index
    ref_field: str | None = None  # array field of keys this source points at
    ref_target: str | None = None  # source.name those refs resolve to

    def filterable_args(self) -> list[str]:
        return [f.arg for f in self.filter_fields]

    def field(self, arg: str) -> FilterField | None:
        for f in self.filter_fields:
            if f.arg == arg:
                return f
        return None


def _gh_repo(value: str) -> str:
    """Accept 'agent-svc' or 'AsatoCorp/agent-svc'; store the full name."""
    return value if "/" in value else f"AsatoCorp/{value}"


_JIRA = SourceSpec(
    name="jira",
    collection="jira_tickets",
    label="JIRA ticket",
    # AS-4163, ENG-12 — a project prefix then a number.
    key_pattern=r"^[A-Z][A-Z0-9]+-\d+$",
    projection={
        "key": 1,
        "summary": 1,
        "status": 1,
        "assignee": 1,
        "issuetype": 1,
        "epic_key": 1,
        "parent_key": 1,
        "labels": 1,
        "updated": 1,
        "url": 1,
    },
    sort_field="updated",
    filter_fields=(
        FilterField("status", "status", help="e.g. 'To Do', 'In Progress', 'Done'"),
        FilterField("assignee", "assignee", USER, identity="jira.displayName"),
        FilterField("label", "labels", help="any label, e.g. 'apr-bug-bash'"),
        FilterField("issuetype", "issuetype", help="'Task', 'Story', 'Bug', 'Epic'"),
        FilterField("epic_key", "epic_key", help="parent epic key, e.g. 'AS-1000'"),
        FilterField("fix_version", "fix_versions", help="release name, e.g. 'Sprint 24'"),
    ),
    description="Asato JIRA tickets (project AS) — what we plan to do.",
    # JIRA is the link hub: other sources point at it; it has no outbound ref_field.
)

_GITHUB = SourceSpec(
    name="github",
    collection="github_prs",
    label="GitHub PR",
    key_pattern=r"#\d+$",  # org/repo#123
    projection={
        "key": 1,
        "repo": 1,
        "summary": 1,
        "status": 1,
        "author": 1,
        "jira_keys": 1,
        "updated": 1,
        "url": 1,
        "merged": 1,
    },
    sort_field="updated",
    filter_fields=(
        FilterField(
            "repo", "repo", help="'agent-svc' or 'AsatoCorp/agent-svc'", normalize=_gh_repo
        ),
        FilterField("status", "status", help="'open' | 'closed' | 'merged' | 'draft'"),
        FilterField("author", "author", USER, identity="github.login"),
        FilterField(
            "has_jira_link", "jira_keys", HAS_REFS, help="True = only PRs linked to a ticket"
        ),
    ),
    description="AsatoCorp GitHub PRs — code that does the work.",
    ref_field="jira_keys",
    ref_target="jira",
)

_SLACK = SourceSpec(
    name="slack",
    collection="slack_messages",
    label="Slack message",
    key_pattern=r"^[^#]+/\d+\.\d+$",  # channel_id/ts, e.g. C0AB/1700000000.123
    projection={
        "key": 1,
        "channel_name": 1,
        "author_name": 1,
        "summary": 1,
        "jira_keys": 1,
        "thread_ts": 1,
        "is_thread_reply": 1,
        "updated": 1,
        "url": 1,
    },
    sort_field="updated",
    filter_fields=(
        FilterField("channel", "channel_name", help="channel name without '#', e.g. 'engineering'"),
        FilterField("author", "author_name", USER, identity="slack.display_name"),
        FilterField(
            "has_jira_link", "jira_keys", HAS_REFS, help="True = only messages mentioning a ticket"
        ),
    ),
    description="Slack messages — what the team is discussing.",
    ref_field="jira_keys",
    ref_target="jira",
)

#: The registry. Append a SourceSpec here to onboard a new connector.
SOURCES: tuple[SourceSpec, ...] = (_JIRA, _GITHUB, _SLACK)

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


def source_for_key(key: str) -> SourceSpec | None:
    """Identify which source a key belongs to by its shape.

    Patterns are mutually exclusive (a '#' marks a PR, a 'channel/ts' marks a
    Slack message, a 'AS-123' marks a ticket); the first match wins.
    """
    for spec in SOURCES:
        if _compiled(spec.key_pattern).search(key):
            return spec
    return None


def referrers_to(target: str) -> list[SourceSpec]:
    """Sources whose docs point at `target` (for inbound link resolution)."""
    return [s for s in SOURCES if s.ref_target == target and s.ref_field]
