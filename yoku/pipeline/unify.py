"""Project source collections into the unified `documents` collection.

This is the Way B projection step. It reads each already-ingested source
collection, runs its mapper, and upserts canonical `UnifiedDoc`s into one
`documents` collection — keyed by canonical key, so re-runs are idempotent.

It is deliberately a separate pass (not wired into each connector's ingest hot
path) so adopting Way B is additive and reversible: the source collections,
ingest, and agent routing are untouched. Connectors can dual-write later.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo import UpdateOne

from yoku.core.constants import INGEST_BATCH_SIZE
from yoku.core.logging import get_logger
from yoku.core.storage.mongo import documents_collection, get_collection
from yoku.pipeline.mappers import SOURCE_MAPPERS

log = get_logger("unify")


def unify_all() -> dict[str, int]:
    """Re-project every mapped source into `documents`. Returns per-source counts."""
    target = documents_collection()
    now = datetime.now(UTC)
    counts: dict[str, int] = {}

    for source_collection, mapper in SOURCE_MAPPERS.items():
        batch: list[UpdateOne] = []
        n = 0
        for raw in get_collection(source_collection).find({}):
            doc = mapper(raw).model_dump()
            # Reuse the per-source embedding: canonical `text` equals the
            # source `text`, so the vector is valid — no re-embedding needed.
            doc["embedding"] = raw.get("embedding")
            doc["_unified_at"] = now
            doc["_source_collection"] = source_collection
            batch.append(UpdateOne({"key": doc["key"]}, {"$set": doc}, upsert=True))
            n += 1
            if len(batch) >= INGEST_BATCH_SIZE:
                target.bulk_write(batch, ordered=False)
                batch = []
        if batch:
            target.bulk_write(batch, ordered=False)
        counts[source_collection] = n
        log.info("unified %s -> documents: %d", source_collection, n)

    return counts
