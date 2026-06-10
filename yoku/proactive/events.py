"""Event delta — Phase 1 of the proactive engine (docs/yoku_agent.md §4).

Mechanism: **diff-on-upsert**. Every connector ingest already reads the
existing doc before upserting (to decide whether to clear the embedding);
ingest widens that projection to this module's `TRACKED_FIELDS` and calls
`diff_events` with both versions — the prior state is in hand for free, so
there is no snapshot collection and no spurious events on the first sync
after deploy (pre-existing docs are simply not "created").

Event kinds:
  created  doc seen for the first time           (one row, no field)
  updated  a tracked field changed between syncs (one row per field)
  linked   a new cross-source link materialized  (emitted by pr_to_jira)

Events are engine-internal raw material for detectors (Phase 2): the
collection is NOT on the agent's `ALLOWED_COLLECTIONS` whitelist. Rows carry
`processed: false`; the proactive loop flips it as it consumes them. They
also back trend analytics (feature-roadmap.md #3) — one delta stream, both
consumers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from yoku.db.mongo import events_collection
from yoku.logging import get_logger

log = get_logger("events")

#: collection -> fields whose changes are worth an `updated` event.
#: Slack is created-only: messages are effectively immutable (the thread
#: rollup rewrites parent text, but that is engine plumbing, not a change
#: anyone should be notified about).
TRACKED_FIELDS: dict[str, tuple[str, ...]] = {
    "dc-jira": ("status", "assignee", "summary"),
    "dc-github": ("status", "summary"),
    "dc-slack": (),
}

#: dc-* collection -> canonical key prefix (mirrors the unify mappers).
_PROVIDER: dict[str, str] = {
    "dc-jira": "jira",
    "dc-github": "github",
    "dc-slack": "slack",
}

# Cap stored old/new values so a long summary can't bloat the events stream.
_VALUE_MAX_CHARS = 300


def _clip(value: object) -> object:
    if isinstance(value, str) and len(value) > _VALUE_MAX_CHARS:
        return value[:_VALUE_MAX_CHARS]
    return value


def _row(kind: str, collection: str, key: str, ts: datetime, **extra: object) -> dict:
    provider = _PROVIDER.get(collection)
    return {
        "kind": kind,
        "doc_key": key,
        "canonical_key": f"{provider}/{key}" if provider else key,
        "collection": collection,
        "field": None,
        "old": None,
        "new": None,
        "ts": ts,
        "processed": False,
        **extra,
    }


def diff_events(
    collection: str,
    key: str,
    existing: dict | None,
    doc: dict,
    now: datetime | None = None,
) -> list[dict]:
    """Events for one upsert: `created` when the doc is new, else one
    `updated` per tracked field whose value changed. Pure — writes nothing.
    """
    ts = now or datetime.now(UTC)
    if existing is None:
        return [_row("created", collection, key, ts)]
    events: list[dict] = []
    for field in TRACKED_FIELDS.get(collection, ()):
        old, new = existing.get(field), doc.get(field)
        if old != new:
            events.append(
                _row("updated", collection, key, ts, field=field, old=_clip(old), new=_clip(new))
            )
    return events


def linked_event(
    jira_key: str,
    pr_key: str,
    now: datetime | None = None,
) -> dict:
    """A new PR↔ticket link materialized (emitted by the pr_to_jira pass)."""
    ts = now or datetime.now(UTC)
    return _row("linked", "dc-jira", jira_key, ts, field="linked_prs", new=pr_key)


def write_events(events: list[dict]) -> int:
    """Bulk-insert staged event rows. Returns the count written."""
    if not events:
        return 0
    events_collection().insert_many(events, ordered=False)
    log.info("wrote %d event(s)", len(events))
    return len(events)
