"""Pure-logic unit tests for the pipeline modules (embed / pr_to_jira / consistency).

Collections and the OpenAI client are faked, so these run with no network/mongo.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from yoku.pipeline import consistency, embed, pr_to_jira


class _FakeEmbedColl:
    name = "jira_tickets"

    def __init__(self, docs):
        self._docs = docs
        self.written = []

    def count_documents(self, query):
        return len(self._docs)

    def find(self, query, projection=None):
        return iter(self._docs)

    def bulk_write(self, ops, ordered=False):
        self.written.extend(ops)


class _FakeOpenAI:
    def __init__(self):
        self.embeddings = SimpleNamespace(create=self._create)
        self.calls = 0

    def _create(self, model, input):
        self.calls += 1
        data = [SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in input]
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=42))


@pytest.mark.unit
def test_embed_flush_writes_one_op_per_doc():
    coll = _FakeEmbedColl([])
    client = _FakeOpenAI()
    batch = [{"key": "AS-1", "text": "alpha"}, {"key": "AS-2", "text": "beta"}]
    n, tokens = embed._flush(client, coll, batch)
    assert n == 2
    assert tokens == 42
    assert len(coll.written) == 2


@pytest.mark.unit
def test_embed_collection_skips_empty_text():
    docs = [{"key": "AS-1", "text": "real"}, {"key": "AS-2", "text": "   "}]
    coll = _FakeEmbedColl(docs)
    client = _FakeOpenAI()
    total, tokens = embed._embed_collection(coll, client, re_embed_all=True)
    assert total == 1


@pytest.mark.unit
def test_embed_collection_returns_zero_when_nothing_to_do():
    coll = _FakeEmbedColl([])
    client = _FakeOpenAI()
    assert embed._embed_collection(coll, client, re_embed_all=False) == (0, 0)


@pytest.mark.unit
def test_embed_parse_args_defaults_to_all_embeddable(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["embed"])
    colls, re_all = embed._parse_args()
    assert "jira_tickets" in colls
    assert re_all is False


@pytest.mark.unit
def test_embed_parse_args_single_collection(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["embed", "--coll", "github_prs", "--all"])
    colls, re_all = embed._parse_args()
    assert colls == ["github_prs"]
    assert re_all is True


@pytest.mark.unit
def test_embed_parse_args_rejects_unknown_collection(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["embed", "--coll", "bogus"])
    with pytest.raises(SystemExit):
        embed._parse_args()


class _FindColl:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query, projection=None):
        return iter(self._docs)


@pytest.mark.unit
def test_consistency_flags_done_without_pr(monkeypatch):
    tickets = _FindColl(
        [
            {"key": "AS-1", "status": "Done", "linked_prs": []},
            {"key": "AS-2", "status": "Done", "linked_prs": [{"key": "x"}]},
            {"key": "AS-3", "status": "In Progress", "linked_prs": []},
        ]
    )
    prs = _FindColl([])
    monkeypatch.setattr(consistency, "tickets_collection", lambda: tickets)
    monkeypatch.setattr(consistency, "github_prs_collection", lambda: prs)

    report = consistency.consistency_report(done_statuses=frozenset({"Done"}))
    assert report["done_without_pr"]["count"] == 1
    assert report["done_without_pr"]["sample"] == ["AS-1"]


@pytest.mark.unit
def test_consistency_flags_merged_without_ticket(monkeypatch):
    tickets = _FindColl([])
    prs = _FindColl(
        [
            {"key": "o/r#1", "status": "merged", "jira_keys": []},
            {"key": "o/r#2", "status": "merged", "jira_keys": ["AS-1"]},
            {"key": "o/r#3", "status": "open", "jira_keys": []},
        ]
    )
    monkeypatch.setattr(consistency, "tickets_collection", lambda: tickets)
    monkeypatch.setattr(consistency, "github_prs_collection", lambda: prs)

    report = consistency.consistency_report()
    assert report["merged_without_ticket"]["count"] == 1
    assert report["merged_without_ticket"]["sample"] == ["o/r#1"]


@pytest.mark.unit
def test_consistency_sample_cap(monkeypatch):
    tickets = _FindColl([{"key": f"AS-{i}", "status": "Done", "linked_prs": []} for i in range(20)])
    monkeypatch.setattr(consistency, "tickets_collection", lambda: tickets)
    monkeypatch.setattr(consistency, "github_prs_collection", lambda: _FindColl([]))

    report = consistency.consistency_report(done_statuses=frozenset({"Done"}), sample=3)
    assert report["done_without_pr"]["count"] == 20
    assert len(report["done_without_pr"]["sample"]) == 3


class _LinkColl:
    def __init__(self, docs=None):
        self._docs = docs or []
        self.bulk_ops = []

    def find(self, query, projection=None):
        return iter(self._docs)

    def update_many(self, *a, **k):
        return SimpleNamespace(modified_count=0)

    def bulk_write(self, ops, ordered=True):
        self.bulk_ops.extend(ops)
        return SimpleNamespace()


@pytest.mark.unit
def test_pr_to_jira_links_each_referenced_ticket(monkeypatch):
    jira = _LinkColl()
    gh = _LinkColl(
        [
            {
                "key": "o/r#1",
                "jira_keys": ["AS-1", "AS-2"],
                "status": "merged",
                "url": "u",
                "summary": "s",
                "repo": "r",
                "merged": True,
            }
        ]
    )
    monkeypatch.setattr(pr_to_jira, "tickets_collection", lambda: jira)
    monkeypatch.setattr(pr_to_jira, "github_prs_collection", lambda: gh)
    monkeypatch.setattr(sys, "argv", ["link_prs_to_jira"])

    pr_to_jira.main()
    assert len(jira.bulk_ops) == 4


@pytest.mark.unit
def test_pr_to_jira_no_prs_writes_nothing(monkeypatch):
    jira = _LinkColl()
    gh = _LinkColl([])
    monkeypatch.setattr(pr_to_jira, "tickets_collection", lambda: jira)
    monkeypatch.setattr(pr_to_jira, "github_prs_collection", lambda: gh)
    monkeypatch.setattr(sys, "argv", ["link_prs_to_jira"])

    pr_to_jira.main()
    assert jira.bulk_ops == []
