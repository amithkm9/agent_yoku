"""Canonical document models — Way B's per-primitive schemas.

Generated from `yoku/schemas/openapi/` (the source of truth). This module
re-exports them so existing imports keep working; do not add fields here —
edit the YAML and run `make models`.

Shape: three independent primitives (`WorkItem`, `PullRequest`, `Conversation`),
each fully self-contained with all shared fields inlined.
"""

from __future__ import annotations

from yoku.constants import Primitive
from yoku.schemas._generated.conversation import Conversation
from yoku.schemas._generated.pull_request import PullRequest
from yoku.schemas._generated.work_item import WorkItem

__all__ = [
    "Conversation",
    "Primitive",
    "PullRequest",
    "WorkItem",
]
