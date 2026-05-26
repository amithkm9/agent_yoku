"""Hybrid semantic_search: vector + idf-keyword fused with RRF.

Hermetic — collections are faked and the query embedder is stubbed, so cosine
is fully controlled and we can prove the keyword half changes the ranking.
"""

from __future__ import annotations

import numpy as np
import pytest

from agent_yoku.agent import tools


def _seed_index(monkeypatch, jira=None, github=None, query_vec=(1.0, 0.0, 0.0)):
    """Populate the lazy index from fake docs and pin the query embedding."""
    monkeypatch.setattr(tools, "tickets_collection", lambda: _FakeColl(jira or []))
    monkeypatch.setattr(tools, "github_prs_collection", lambda: _FakeColl(github or []))
    monkeypatch.setattr(tools, "_embed_query", lambda _q: np.array(query_vec, dtype=np.float32))


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    def find(self, *_a, **_k):
        return list(self._docs)


@pytest.mark.unit
def test_tokenize_keeps_whole_and_splits_identifiers():
    assert {"as-1234", "as", "1234"} <= tools._tokenize("AS-1234")
    assert {"dc-okta-user", "dc", "okta", "user"} <= tools._tokenize("dc-okta-user")


@pytest.mark.unit
def test_exact_key_beats_higher_cosine(isolated_index, monkeypatch):
    # A has perfect cosine but no token overlap with the query.
    # B is cosine-orthogonal but its key matches the query exactly.
    jira = [
        {"key": "TASK-1000", "summary": "service mapper migration", "embedding": [1.0, 0.0, 0.0]},
        {"key": "AS-9999", "summary": "unrelated billing thing", "embedding": [0.0, 1.0, 0.0]},
    ]
    _seed_index(monkeypatch, jira=jira, query_vec=(1.0, 0.0, 0.0))

    out = tools.semantic_search.invoke({"query": "AS-9999", "k": 5})
    keys = [c["key"] for c in out]

    assert keys[0] == "AS-9999", "exact key match should win via RRF"
    assert "TASK-1000" in keys, "semantic hit is still retained, just lower"


@pytest.mark.unit
def test_repo_name_match_surfaces_pr(isolated_index, monkeypatch):
    github = [
        {
            "key": "AsatoCorp/other#1",
            "summary": "x",
            "repo": "other-svc",
            "embedding": [1.0, 0.0, 0.0],
        },
        {
            "key": "AsatoCorp/dc-okta-user#7",
            "summary": "x",
            "repo": "dc-okta-user",
            "embedding": [0.0, 1.0, 0.0],
        },
    ]
    _seed_index(monkeypatch, github=github, query_vec=(1.0, 0.0, 0.0))

    out = tools.semantic_search.invoke({"query": "dc-okta-user", "k": 5, "source": "github"})

    assert out[0]["key"] == "AsatoCorp/dc-okta-user#7"


@pytest.mark.unit
def test_falls_back_to_pure_cosine_when_no_keyword_hit(isolated_index, monkeypatch):
    jira = [
        {"key": "AS-1", "summary": "alpha", "embedding": [1.0, 0.0, 0.0]},
        {"key": "AS-2", "summary": "beta", "embedding": [0.0, 1.0, 0.0]},
    ]
    _seed_index(monkeypatch, jira=jira, query_vec=(1.0, 0.0, 0.0))

    # "zzz" matches no token, so ordering is the cosine ranking.
    out = tools.semantic_search.invoke({"query": "zzz", "k": 5})

    assert out[0]["key"] == "AS-1"


@pytest.mark.unit
def test_source_filter_excludes_other_source(isolated_index, monkeypatch):
    jira = [{"key": "AS-1", "summary": "shared term", "embedding": [1.0, 0.0, 0.0]}]
    github = [
        {
            "key": "AsatoCorp/r#1",
            "summary": "shared term",
            "repo": "r",
            "embedding": [1.0, 0.0, 0.0],
        }
    ]
    _seed_index(monkeypatch, jira=jira, github=github, query_vec=(1.0, 0.0, 0.0))

    out = tools.semantic_search.invoke({"query": "shared term", "k": 5, "source": "jira"})

    assert {c["source"] for c in out} == {"jira"}


@pytest.mark.unit
def test_empty_query_raises(isolated_index, monkeypatch):
    jira = [{"key": "AS-1", "summary": "x", "embedding": [1.0, 0.0, 0.0]}]
    _seed_index(monkeypatch, jira=jira)
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            tools.semantic_search.invoke({"query": bad})


@pytest.mark.unit
def test_source_with_no_docs_returns_empty(isolated_index, monkeypatch):
    # Only GitHub docs exist; asking for jira yields nothing rather than leaking.
    github = [{"key": "AsatoCorp/r#1", "summary": "x", "repo": "r", "embedding": [1.0, 0.0, 0.0]}]
    _seed_index(monkeypatch, github=github)

    assert tools.semantic_search.invoke({"query": "anything", "k": 5, "source": "jira"}) == []


@pytest.mark.unit
def test_k_is_clamped(isolated_index, monkeypatch):
    jira = [{"key": f"AS-{i}", "summary": "alpha", "embedding": [1.0, 0.0, 0.0]} for i in range(5)]
    _seed_index(monkeypatch, jira=jira)

    assert len(tools.semantic_search.invoke({"query": "alpha", "k": 2})) == 2
    # k below 1 and above the cap are clamped, never raising.
    assert len(tools.semantic_search.invoke({"query": "alpha", "k": 0})) == 1
    assert len(tools.semantic_search.invoke({"query": "alpha", "k": 9999})) == 5


@pytest.mark.unit
def test_ubiquitous_token_dropped_at_scale(isolated_index, monkeypatch):
    # On a corpus past the stopword floor, a token in (almost) every doc — like
    # the `as` project prefix — is dropped from the keyword index, while a rare
    # token survives. (At tiny corpora the floor keeps everything, by design.)
    jira = [
        {"key": f"AS-{i}", "summary": "shared", "embedding": [1.0, 0.0, 0.0]} for i in range(150)
    ]
    jira[0]["summary"] = "shared uniqueword"
    _seed_index(monkeypatch, jira=jira)
    tools._ensure_index()

    inverted = tools._INDEX["inverted"]
    assert "shared" not in inverted, "ubiquitous token should be stopworded"
    assert "as" not in inverted, "project prefix should be stopworded"
    assert "uniqueword" in inverted, "rare token should be kept"
