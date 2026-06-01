"""Integration test for the unify projection into the ds-* canonical collections."""

from __future__ import annotations

from yoku.pipeline.unify import unify_all


def _seed(store):
    store["sources"]["dc-jira"].insert_one(
        {"key": "AS-1", "summary": "t", "status": "Done", "linked_prs": ["o/r#1"]}
    )
    store["sources"]["dc-github"].insert_one(
        {"key": "o/r#1", "repo": "o/r", "number": 1, "status": "merged", "jira_keys": ["AS-1"]}
    )
    store["sources"]["dc-slack"].insert_one({"key": "C/1.2", "text": "hi", "jira_keys": ["AS-1"]})


def test_unify_projects_every_source(fake_store):
    _seed(fake_store)

    counts = unify_all()

    assert counts == {"dc-jira": 1, "dc-github": 1, "dc-slack": 1}
    # Each domain lands in its own ds-* store
    assert fake_store["ds_work_item"].count_documents() == 1
    assert fake_store["ds_pull_request"].count_documents() == 1
    assert fake_store["ds_conversation"].count_documents() == 1
    all_docs = fake_store["documents"].find()
    assert {d["domain"] for d in all_docs} == {"work_item", "pull_request", "conversation"}


def test_unify_namespaces_keys_and_refs(fake_store):
    _seed(fake_store)
    unify_all()

    pr = fake_store["documents"].find_one({"key": "github/o/r#1"})
    assert pr is not None
    assert pr["domain"] == "pull_request"
    assert pr["refs"] == ["jira/AS-1"]


def test_unify_is_idempotent(fake_store):
    _seed(fake_store)
    unify_all()
    unify_all()

    assert fake_store["documents"].count_documents() == 3


def test_unify_carries_source_embedding(fake_store):
    """The per-source embedding is reused on the canonical doc (no re-embedding)."""
    vec = [0.1, 0.2, 0.3]
    fake_store["sources"]["dc-github"].insert_one(
        {"key": "o/r#9", "repo": "o/r", "number": 9, "text": "blob", "embedding": vec}
    )

    unify_all()

    doc = fake_store["documents"].find_one({"key": "github/o/r#9"})
    assert doc is not None
    assert doc["embedding"] == vec


def test_unify_sets_embedding_none_when_source_unembedded(fake_store):
    """A source doc with no embedding yields embedding=None on the canonical doc."""
    fake_store["sources"]["dc-github"].insert_one(
        {"key": "o/r#10", "repo": "o/r", "number": 10, "text": "blob"}
    )

    unify_all()

    doc = fake_store["documents"].find_one({"key": "github/o/r#10"})
    assert doc is not None
    assert doc["embedding"] is None
