"""Canonical document models — Way B's per-primitive schemas.

These are GENERATED from `yoku/schemas_openapi/documents.yaml` (the source of
truth), mirroring AsatoCorp's `schemas_openapi -> generated Pydantic` pattern.
This module re-exports them so existing imports (`yoku.core.models.unified`)
keep working; do not add fields here — edit the YAML and run `make models`.

Shape: a shared `BaseDoc` core + typed primitive subclasses (`WorkItem`,
`PullRequest`, `Conversation`); `UnifiedDoc` is the union of all primitives, the
schema of the single polymorphic `documents` collection.
"""

from __future__ import annotations

from yoku.schemas._generated.documents import (
    BaseDoc,
    Conversation,
    Primitive,
    PullRequest,
    UnifiedDoc,
    WorkItem,
)

__all__ = [
    "BaseDoc",
    "Conversation",
    "Primitive",
    "PullRequest",
    "UnifiedDoc",
    "WorkItem",
]
