# NOT auto-generated — UnifiedDoc is Python-only (no YAML source).
# It is the union of all primitives used by the schema registry to expose
# every primitive's filterable fields on the single `documents` collection.

from __future__ import annotations

from pydantic import ConfigDict

from yoku.schemas._generated.conversation import Conversation
from yoku.schemas._generated.pull_request import PullRequest
from yoku.schemas._generated.work_item import WorkItem


class UnifiedDoc(WorkItem, PullRequest, Conversation):
    """Union of every primitive — the schema of the polymorphic `documents`
    collection. A stored document is really one primitive (its `domain` says
    which); this union exists so the schema registry exposes every primitive's
    fields as filterable on the single `documents` collection. Unset fields for
    a given primitive are simply absent."""

    model_config = ConfigDict(extra="allow")
