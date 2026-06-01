"""Unit tests for entity_links derivation from canonical doc refs."""

from __future__ import annotations

from yoku.pipeline.entity_links import build_entity_links, link_type_for, links_from_doc


def test_link_type_by_domain():
    assert link_type_for("pull_request") == "implements"
    assert link_type_for("conversation") == "discusses"
    assert link_type_for("work_item") == "tracks"
    assert link_type_for("something_new") == "references"
    assert link_type_for(None) == "references"


def test_links_from_doc_emits_one_row_per_ref():
    doc = {
        "key": "github/o/r#1",
        "domain": "pull_request",
        "provider": "github",
        "refs": ["jira/AS-1", "jira/AS-2"],
    }
    rows = links_from_doc(doc)
    assert [r["to_key"] for r in rows] == ["jira/AS-1", "jira/AS-2"]
    assert all(r["from_key"] == "github/o/r#1" for r in rows)
    assert all(r["link_type"] == "implements" for r in rows)
    assert all(r["extractor"] == "refs" and r["confidence"] == 1.0 for r in rows)


def test_links_from_doc_dedupes_and_handles_empty():
    assert links_from_doc({"key": "k", "refs": []}) == []
    assert links_from_doc({"refs": ["x"]}) == []  # no from key
    dup = links_from_doc({"key": "k", "domain": "work_item", "refs": ["a", "a", "b"]})
    assert [r["to_key"] for r in dup] == ["a", "b"]


class _PairColl:
    """Minimal links-collection fake: upsert keyed by (from_key, to_key)."""

    def __init__(self):
        self.by_pair: dict[tuple, dict] = {}

    def bulk_write(self, ops, ordered=False):
        for op in ops:
            f = op._filter
            self.by_pair[(f["from_key"], f["to_key"])] = dict(op._doc["$set"])

    def count_documents(self, *a, **k):
        return len(self.by_pair)


def test_build_entity_links_is_idempotent(fake_store, monkeypatch):
    from yoku.pipeline import entity_links as mod

    # Seed documents directly into the ds-* stores via the composite view
    fake_store["documents"].insert_one(
        {
            "key": "github/o/r#1",
            "domain": "pull_request",
            "provider": "github",
            "refs": ["jira/AS-1"],
        }
    )
    fake_store["documents"].insert_one(
        {"key": "slack/C/1.2", "domain": "conversation", "provider": "slack", "refs": ["jira/AS-1"]}
    )
    links = _PairColl()
    monkeypatch.setattr(mod, "ds_work_item_collection", lambda: fake_store["ds_work_item"])
    monkeypatch.setattr(mod, "ds_pull_request_collection", lambda: fake_store["ds_pull_request"])
    monkeypatch.setattr(mod, "ds_conversation_collection", lambda: fake_store["ds_conversation"])
    monkeypatch.setattr(mod, "ds_entity_links_collection", lambda: links)

    first = build_entity_links()
    second = build_entity_links()

    assert first == 2
    assert second == 2  # same edges, re-upserted
    assert links.count_documents() == 2  # deduped by (from_key, to_key)
