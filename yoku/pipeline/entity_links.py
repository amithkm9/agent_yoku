"""Derive the `entity_links` collection from canonical document `refs`.

Way B Phase 2 groundwork. Every canonical doc already carries `refs` — the
canonical keys of items it points at across sources (a PR's tickets, a Slack
message's tickets, a ticket's PRs). This pass turns each of those edges into one
typed `entity_links` row, deduped by (from_key, to_key) so re-runs are idempotent.

It is intentionally additive and read-only over `documents`: it does not touch
unify, the agent, or the source collections. Link typing is deliberately coarse
for now (derived from the source domain) — richer typing and non-`refs` edges
(commit SHA, release, time correlation) are later steps.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo import UpdateOne

from yoku.core.constants import LINK_BATCH_SIZE
from yoku.core.logging import get_logger
from yoku.core.storage.mongo import (
    ds_conversation_collection,
    ds_entity_links_collection,
    ds_pull_request_collection,
    ds_work_item_collection,
)

log = get_logger("entity_links")

#: Coarse link type by the referring doc's primitive. A PR that references a
#: work item *implements* it; a conversation that references one *discusses* it.
#: Anything else falls back to a generic "references".
_LINK_TYPE_BY_DOMAIN: dict[str, str] = {
    "pull_request": "implements",
    "conversation": "discusses",
    "work_item": "tracks",
    "event": "relates_to",
    "document": "documents",
}


def link_type_for(domain: str | None) -> str:
    """Map a referring doc's primitive (`domain`) to a coarse link type."""
    return _LINK_TYPE_BY_DOMAIN.get(domain or "", "references")


def links_from_doc(doc: dict) -> list[dict]:
    """Derive entity_links rows from one canonical doc's `refs`. Pure; no I/O."""
    from_key = doc.get("key")
    if not from_key:
        return []
    link_type = link_type_for(doc.get("domain"))
    rows: list[dict] = []
    seen: set[str] = set()
    for to_key in doc.get("refs") or []:
        if not to_key or to_key in seen:
            continue
        seen.add(to_key)
        rows.append(
            {
                "from_key": from_key,
                "to_key": to_key,
                "link_type": link_type,
                "context": f"{doc.get('provider', '?')} {doc.get('domain', '?')} refs",
                "confidence": 1.0,
                "extractor": "refs",
            }
        )
    return rows


def build_entity_links() -> int:
    """Materialise `ds-entity-links` from every canonical doc's `refs`. Idempotent.

    Reads from all three ds-* content collections and writes typed edges.
    Returns the number of edges upserted.
    """
    target = ds_entity_links_collection()
    now = datetime.now(UTC)
    batch: list[UpdateOne] = []
    total = 0

    source_collections = [
        ds_work_item_collection(),
        ds_pull_request_collection(),
        ds_conversation_collection(),
    ]
    for source_coll in source_collections:
        for doc in source_coll.find({"refs": {"$ne": []}}, {"_id": 0}):
            for row in links_from_doc(doc):
                row["_built_at"] = now
                batch.append(
                    UpdateOne(
                        {"from_key": row["from_key"], "to_key": row["to_key"]},
                        {"$set": row},
                        upsert=True,
                    )
                )
                if len(batch) >= LINK_BATCH_SIZE:
                    target.bulk_write(batch, ordered=False)
                    total += len(batch)
                    batch = []
    if batch:
        target.bulk_write(batch, ordered=False)
        total += len(batch)

    log.info("entity_links built: %d edges", total)
    return total
