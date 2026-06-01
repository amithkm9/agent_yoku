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
        "dc-jira": FakeColl(),
        "dc-github": FakeColl(),
        "dc-slack": FakeColl(),
    }
    ds_work_item = FakeColl()
    ds_pull_request = FakeColl()
    ds_conversation = FakeColl()

    monkeypatch.setattr(
        unify_mod, "dc_jira_collection", lambda: sources["dc-jira"]
    )
    monkeypatch.setattr(
        unify_mod, "dc_github_collection", lambda: sources["dc-github"]
    )
    monkeypatch.setattr(
        unify_mod, "dc_slack_collection", lambda: sources["dc-slack"]
    )
    monkeypatch.setattr(unify_mod, "ds_work_item_collection", lambda: ds_work_item)
    monkeypatch.setattr(unify_mod, "ds_pull_request_collection", lambda: ds_pull_request)
    monkeypatch.setattr(unify_mod, "ds_conversation_collection", lambda: ds_conversation)

    # Patch the module-level _SOURCE_COLLECTIONS dict so unify_all uses the fakes.
    monkeypatch.setattr(
        unify_mod,
        "_SOURCE_COLLECTIONS",
        {
            "dc-jira": lambda: sources["dc-jira"],
            "dc-github": lambda: sources["dc-github"],
            "dc-slack": lambda: sources["dc-slack"],
        },
    )
    # Patch _DOMAIN_TARGETS to use the fakes.
    monkeypatch.setattr(
        unify_mod,
        "_DOMAIN_TARGETS",
        {
            "work_item": lambda: ds_work_item,
            "pull_request": lambda: ds_pull_request,
            "conversation": lambda: ds_conversation,
        },
    )

    # Composite "documents" view — used by entity_links tests for simplicity.
    class _CompositeView:
        """Union view of all three ds-* content stores (for entity_links tests)."""

        def insert_one(self, doc: dict) -> None:
            domain = doc.get("domain")
            if domain == "work_item":
                ds_work_item.insert_one(doc)
            elif domain == "pull_request":
                ds_pull_request.insert_one(doc)
            else:
                ds_conversation.insert_one(doc)

        def find(self, *a, **k):
            return (
                ds_work_item.find()
                + ds_pull_request.find()
                + ds_conversation.find()
            )

        def find_one(self, flt: dict, *a, **k):
            for store in (ds_work_item, ds_pull_request, ds_conversation):
                result = store.find_one(flt)
                if result:
                    return result
            return None

        def count_documents(self, *a, **k) -> int:
            return (
                ds_work_item.count_documents()
                + ds_pull_request.count_documents()
                + ds_conversation.count_documents()
            )

    documents = _CompositeView()

    return {
        "sources": sources,
        "documents": documents,
        "ds_work_item": ds_work_item,
        "ds_pull_request": ds_pull_request,
        "ds_conversation": ds_conversation,
    }
