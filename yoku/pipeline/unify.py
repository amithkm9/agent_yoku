"""Project source collections into the canonical ds-* collections.

This is the Way B projection step. It reads each already-ingested dc-* source
collection, runs its mapper, and upserts canonical primitives (WorkItem /
PullRequest / Conversation) into the appropriate ds-* collection based on
`domain` — keyed by canonical key, so re-runs are idempotent.

It is deliberately a separate pass (not wired into each connector's ingest hot
path) so adopting Way B is additive and reversible: the source collections,
ingest, and agent routing are untouched. Connectors can dual-write later.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo import UpdateOne
from pymongo.collection import Collection

from yoku.constants import INGEST_BATCH_SIZE
from yoku.db.mongo import (
    dc_github_collection,
    dc_jira_collection,
    dc_slack_collection,
    ds_conversation_collection,
    ds_pull_request_collection,
    ds_work_item_collection,
)
from yoku.logging import get_logger
from yoku.mappers import SOURCE_MAPPERS

log = get_logger("unify")

#: domain -> target collection accessor
_DOMAIN_TARGETS: dict[str, object] = {
    "work_item": ds_work_item_collection,
    "pull_request": ds_pull_request_collection,
    "conversation": ds_conversation_collection,
}

#: dc-* source collection accessor -> collection name key used in SOURCE_MAPPERS
_SOURCE_COLLECTIONS: dict[str, object] = {
    "dc-jira": dc_jira_collection,
    "dc-github": dc_github_collection,
    "dc-slack": dc_slack_collection,
}


def _target_for_domain(domain: str | None) -> Collection | None:
    """Return the ds-* collection for a given domain, or None if unknown."""
    factory = _DOMAIN_TARGETS.get(domain or "")
    return factory() if factory else None  # type: ignore[operator]


def unify_all() -> dict[str, int]:
    """Re-project every mapped source into the appropriate ds-* collection.

    Returns per-source counts. Docs with unknown domains are skipped (logged
    as a warning).
    """
    now = datetime.now(UTC)
    counts: dict[str, int] = {}

    for source_collection, mapper in SOURCE_MAPPERS.items():
        source_factory = _SOURCE_COLLECTIONS.get(source_collection)
        if source_factory is None:
            log.warning("no source accessor for %r — skipping", source_collection)
            continue

        # Batch per ds-* target so we can bulk_write into the right collection.
        by_domain: dict[str, list[UpdateOne]] = {}
        n = 0

        for raw in source_factory().find({}):  # type: ignore[operator]
            doc = mapper(raw).model_dump()
            domain = doc.get("domain")
            target = _target_for_domain(domain)
            if target is None:
                log.warning(
                    "unify: unknown domain %r on %r — skipping",
                    domain,
                    doc.get("key"),
                )
                continue

            doc["embedding"] = raw.get("embedding")
            doc["_unified_at"] = now
            doc["_source_collection"] = source_collection
            by_domain.setdefault(domain, []).append(
                UpdateOne({"key": doc["key"]}, {"$set": doc}, upsert=True)
            )
            n += 1

            # Flush when any domain's batch is full.
            for dom, batch in list(by_domain.items()):
                if len(batch) >= INGEST_BATCH_SIZE:
                    _target_for_domain(dom).bulk_write(batch, ordered=False)  # type: ignore[union-attr]
                    by_domain[dom] = []

        # Final flush for remaining batches.
        for dom, batch in by_domain.items():
            if batch:
                _target_for_domain(dom).bulk_write(batch, ordered=False)  # type: ignore[union-attr]

        counts[source_collection] = n
        log.info("unified %s -> ds-*: %d", source_collection, n)

    return counts
