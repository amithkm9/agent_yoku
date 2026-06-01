"""Pipeline-test fixtures: a dependency-free in-memory collection double.

Mirrors the `_FakeCollection` pattern used elsewhere in the suite (no mongomock):
the fake supports just the operations `unify` touches — `find`, `bulk_write`
(UpdateOne upserts keyed by `key`), `count_documents`, and `find_one`.
"""

from __future__ import annotations

import pytest

from yoku.pipeline import unify as unify_mod


class FakeColl:
    def __init__(self, docs=None):
        self.by_key: dict[str, dict] = {}
        for d in docs or []:
            self.insert_one(d)

    def insert_one(self, doc: dict) -> None:
        self.by_key[doc["key"]] = dict(doc)

    def find(self, *args, **kwargs):
        return [dict(d) for d in self.by_key.values()]

    def find_one(self, flt: dict, *args, **kwargs):
        doc = self.by_key.get(flt.get("key"))
        return dict(doc) if doc else None

    def count_documents(self, *args, **kwargs) -> int:
        return len(self.by_key)

    def bulk_write(self, ops, ordered: bool = False) -> None:
        for op in ops:
            key = op._filter["key"]
            self.by_key[key] = dict(op._doc["$set"])


@pytest.fixture
def fake_store(monkeypatch):
    """Wire `unify`'s source + target accessors to in-memory fakes."""
    sources = {
        "jira_tickets": FakeColl(),
        "github_prs": FakeColl(),
        "slack_messages": FakeColl(),
    }
    documents = FakeColl()
    monkeypatch.setattr(unify_mod, "get_collection", lambda name: sources[name])
    monkeypatch.setattr(unify_mod, "documents_collection", lambda: documents)
    return {"sources": sources, "documents": documents}
