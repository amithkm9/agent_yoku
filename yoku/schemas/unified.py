"""Canonical document models — Way B's per-primitive schemas.

Generated from `yoku/schemas/openapi/` (the source of truth). This module
re-exports them so existing imports keep working; do not add fields here —
edit the YAML and run `make models`.

Shape: a shared `BaseDoc` core + typed primitive subclasses (`WorkItem`,
`PullRequest`, `Conversation`); `UnifiedDoc` is the Python-only union of all
primitives, the schema of the single polymorphic `documents` collection.
"""

from __future__ import annotations

from yoku.schemas._generated.base import BaseDoc, Primitive
from yoku.schemas._generated.conversation import Conversation
from yoku.schemas._generated.pull_request import PullRequest
from yoku.schemas._generated.unified_doc import UnifiedDoc
from yoku.schemas._generated.work_item import WorkItem

__all__ = [
    "BaseDoc",
    "Conversation",
    "Primitive",
    "PullRequest",
    "UnifiedDoc",
    "WorkItem",
]
