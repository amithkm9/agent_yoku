"""Unit tests for filter(source="slack", …) and the Slack source in semantic_search."""

from __future__ import annotations

import numpy as np
import pytest

from yoku.agent import tools


class _FakeColl:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, q=None, proj=None):
        return _FakeCursor(self._docs, q or {})


class _FakeCursor:
    def __init__(self, docs, q):
        self._docs = docs
        self._q = q
        self._limit = None

    def sort(self, *_):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def __iter__(self):
        results = [d for d in self._docs if self._matches(d)]
        if self._limit:
            results = results[: self._limit]
        return iter(results)

    def _matches(self, doc: dict) -> bool:
        for k, v in self._q.items():
            if isinstance(v, dict):
                if "$ne" in v and doc.get(k) == v["$ne"]:
                    return False
                if "$in" in v and doc.get(k) not in v["$in"]:
                    return False
                if "$gte" in v and (doc.get(k) or "") < v["$gte"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True


def _patch_collections(monkeypatch, **by_name):
    """Patch the get_collection seam with a name -> fake-collection dispatcher."""
    monkeypatch.setattr(tools, "get_collection", lambda name: by_name[name])


_SLACK_DOCS = [
    {
        "key": "C1/1.0",
        "channel_name": "engineering",
        "author_name": "alice",
        "summary": "AS-1234 is broken",
        "jira_keys": ["AS-1234"],
        "updated": "2024-03-15T10:00:00",
        "url": "https://acme.slack.com/...",
        "thread_ts": None,
        "is_thread_reply": False,
    },
    {
        "key": "C2/2.0",
        "channel_name": "general",
        "author_name": "bob",
        "summary": "lunch plans",
        "jira_keys": [],
        "updated": "2024-03-14T09:00:00",
        "url": "https://acme.slack.com/...",
        "thread_ts": None,
        "is_thread_reply": False,
    },
]


@pytest.mark.unit
def test_filter_slack_by_channel(monkeypatch):
    _patch_collections(monkeypatch, slack_messages=_FakeColl(_SLACK_DOCS))
    results = tools.filter.invoke({"source": "slack", "filters": {"channel": "engineering"}})
    assert len(results) == 1
    assert results[0]["channel_name"] == "engineering"


@pytest.mark.unit
def test_filter_slack_by_author(monkeypatch):
    _patch_collections(monkeypatch, slack_messages=_FakeColl(_SLACK_DOCS))
    monkeypatch.setattr(tools, "_resolve_user_doc", lambda _q: None)
    results = tools.filter.invoke({"source": "slack", "filters": {"author": "bob"}})
    assert len(results) == 1
    assert results[0]["author_name"] == "bob"


@pytest.mark.unit
def test_filter_slack_has_jira_link_true(monkeypatch):
    _patch_collections(monkeypatch, slack_messages=_FakeColl(_SLACK_DOCS))
    results = tools.filter.invoke({"source": "slack", "filters": {"has_jira_link": True}})
    assert all(r["jira_keys"] for r in results)


@pytest.mark.unit
def test_filter_slack_has_jira_link_false(monkeypatch):
    _patch_collections(monkeypatch, slack_messages=_FakeColl(_SLACK_DOCS))
    results = tools.filter.invoke({"source": "slack", "filters": {"has_jira_link": False}})
    assert all(not r["jira_keys"] for r in results)


@pytest.mark.unit
def test_slack_source_included_in_both(isolated_index, monkeypatch):
    jira = [{"key": "AS-1", "summary": "auth bug", "embedding": [1.0, 0.0, 0.0]}]
    slack = [
        {
            "key": "C1/1.0",
            "summary": "auth is broken",
            "channel_name": "eng",
            "author_name": "alice",
            "embedding": [0.9, 0.1, 0.0],
        }
    ]
    _patch_collections(
        monkeypatch,
        jira_tickets=_FakeColl(jira),
        github_prs=_FakeColl([]),
        slack_messages=_FakeColl(slack),
    )
    monkeypatch.setattr(
        tools, "_embed_query", lambda _q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    )

    results = tools.semantic_search.invoke({"query": "auth bug", "k": 10, "source": "both"})
    sources = {r["source"] for r in results}
    assert "jira" in sources
    assert "slack" in sources


@pytest.mark.unit
def test_slack_source_filter_isolates_slack(isolated_index, monkeypatch):
    jira = [{"key": "AS-1", "summary": "auth", "embedding": [1.0, 0.0, 0.0]}]
    slack = [
        {
            "key": "C1/1.0",
            "summary": "auth",
            "channel_name": "eng",
            "author_name": "alice",
            "embedding": [1.0, 0.0, 0.0],
        }
    ]
    _patch_collections(
        monkeypatch,
        jira_tickets=_FakeColl(jira),
        github_prs=_FakeColl([]),
        slack_messages=_FakeColl(slack),
    )
    monkeypatch.setattr(
        tools, "_embed_query", lambda _q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    )

    results = tools.semantic_search.invoke({"query": "auth", "k": 10, "source": "slack"})
    assert all(r["source"] == "slack" for r in results)


@pytest.mark.unit
def test_invalid_source_raises(isolated_index, monkeypatch):
    _patch_collections(
        monkeypatch,
        jira_tickets=_FakeColl([]),
        github_prs=_FakeColl([]),
        slack_messages=_FakeColl([]),
    )
    with pytest.raises(ValueError, match="source must be"):
        tools.semantic_search.invoke({"query": "test", "source": "teams"})
