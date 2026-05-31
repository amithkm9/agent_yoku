"""Generate openai embeddings for any mongo docs missing one.

Usage:
    python embed.py                        # embed missing across jira_tickets + github_prs
    python embed.py --all                  # re-embed everything in both
    python embed.py --coll github_prs      # only this collection
    python embed.py --coll jira_tickets --all
"""

from __future__ import annotations

import sys
import time

from openai import OpenAI
from pymongo import UpdateOne
from pymongo.collection import Collection

from yoku.config import ALLOWED_COLLECTIONS, EMBED_MODEL, openai_client
from yoku.constants import (
    EMBED_BATCH_SIZE,
    EMBED_COST_PER_M_TOKENS,
    EMBED_MAX_CHARS,
    EMBEDDABLE_COLLECTIONS,
)
from yoku.log import get_logger

log = get_logger("embed")


def _parse_args() -> tuple[list[str], bool]:
    args = sys.argv[1:]
    re_embed_all = "--all" in args
    coll_names: list[str] = []
    if "--coll" in args:
        i = args.index("--coll")
        if i + 1 < len(args):
            coll_names = [args[i + 1]]
    if not coll_names:
        coll_names = list(EMBEDDABLE_COLLECTIONS)
    for n in coll_names:
        if n not in ALLOWED_COLLECTIONS:
            raise SystemExit(f"unknown collection: {n}")
    return coll_names, re_embed_all


def _embed_collection(coll: Collection, client: OpenAI, re_embed_all: bool) -> tuple[int, int]:
    query = (
        {} if re_embed_all else {"$or": [{"embedding": None}, {"embedding": {"$exists": False}}]}
    )
    target_count = coll.count_documents(query)
    log.info("coll=%s target=%d re_embed_all=%s", coll.name, target_count, re_embed_all)
    if target_count == 0:
        return 0, 0

    cursor = coll.find(query, {"key": 1, "text": 1})
    pending: list[dict] = []
    total = 0
    total_tokens = 0
    for doc in cursor:
        text = (doc.get("text") or "")[:EMBED_MAX_CHARS].strip()
        if not text:
            continue
        pending.append({"key": doc["key"], "text": text})
        if len(pending) >= EMBED_BATCH_SIZE:
            n, tokens = _flush(client, coll, pending)
            total += n
            total_tokens += tokens
            pending = []
            log.info(
                "coll=%s embedded=%d/%d tokens=%d", coll.name, total, target_count, total_tokens
            )

    if pending:
        n, tokens = _flush(client, coll, pending)
        total += n
        total_tokens += tokens

    return total, total_tokens


def _flush(client: OpenAI, coll: Collection, batch: list[dict]) -> tuple[int, int]:
    resp = client.embeddings.create(
        model=EMBED_MODEL,
        input=[d["text"] for d in batch],
    )
    ops = [
        UpdateOne({"key": d["key"]}, {"$set": {"embedding": r.embedding}})
        for d, r in zip(batch, resp.data)
    ]
    coll.bulk_write(ops, ordered=False)
    tokens = getattr(resp.usage, "total_tokens", 0) if getattr(resp, "usage", None) else 0
    return len(batch), tokens


def main() -> None:
    coll_names, re_embed_all = _parse_args()
    client = openai_client()
    log.info("embed start model=%s collections=%s", EMBED_MODEL, coll_names)
    t0 = time.monotonic()

    grand_total = 0
    grand_tokens = 0
    for name in coll_names:
        coll = ALLOWED_COLLECTIONS[name]()
        n, tokens = _embed_collection(coll, client, re_embed_all)
        grand_total += n
        grand_tokens += tokens

    elapsed = time.monotonic() - t0
    cost = grand_tokens / 1_000_000 * EMBED_COST_PER_M_TOKENS
    log.info(
        "embed done total=%d tokens=%d est_cost=$%.4f elapsed=%.1fs",
        grand_total,
        grand_tokens,
        cost,
        elapsed,
    )


if __name__ == "__main__":
    main()
