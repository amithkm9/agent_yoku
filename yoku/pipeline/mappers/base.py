"""Mapper contract — project a source's stored doc into a `UnifiedDoc`.

A mapper is the Way B equivalent of asato's `dm-*` step, but in-process: a pure
function `stored source doc (dict) -> UnifiedDoc`. It does no I/O — it only
restamps an already-ingested document into the canonical shape and primitive.

Keys live in one namespace, `provider/native_key`, so a `refs` entry on one doc
points at another doc's `key` regardless of which source either came from.
"""

from __future__ import annotations

from collections.abc import Callable

from yoku.core.models import BaseDoc

#: A mapper turns one stored source document into one typed canonical primitive
#: (a `BaseDoc` subclass: WorkItem / PullRequest / Conversation).
Mapper = Callable[[dict], BaseDoc]


def canonical_key(provider: str, native_key: str) -> str:
    """Build a canonical key from a provider and its native key."""
    return f"{provider}/{native_key}"


def ref(provider: str, native_key: str) -> str:
    """Build a cross-source reference (same shape as a canonical key)."""
    return f"{provider}/{native_key}"


def embed_text(title: str | None, body: str | None) -> str | None:
    """Join title + body into one embed-ready blob (None if both empty)."""
    parts = [p for p in (title, body) if p]
    return "\n".join(parts) if parts else None
