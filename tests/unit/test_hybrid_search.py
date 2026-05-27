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

    inverted = tools._index()["inverted"]
    assert "shared" not in inverted, "ubiquitous token should be stopworded"
    assert "as" not in inverted, "project prefix should be stopworded"
    assert "uniqueword" in inverted, "rare token should be kept"


@pytest.mark.unit
def test_lexical_overlap_scores_phrase_and_tokens():
    q = "payment gateway"
    qt = tools._tokenize(q)
    assert tools._lexical_overlap(q, qt, "new payment gateway service") == 1.0
    partial = tools._lexical_overlap(
        "payment timeout", tools._tokenize("payment timeout"), "payment service"
    )
    assert 0.0 < partial < 1.0
    assert tools._lexical_overlap(q, qt, "totally unrelated") == 0.0


@pytest.mark.unit
def test_feature_rerank_promotes_corroborated_doc():
    idx = {
        "docs": [
            {"key": "AS-1", "summary": "same", "source": "jira", "has_links": False},
            {"key": "AS-2", "summary": "same", "source": "jira", "has_links": True},
        ]
    }
    # Equal first-stage score; the cross-linked doc should be reranked first.
    order = tools._feature_rerank("same", [(0, 0.01), (1, 0.01)], idx)
    assert order[0] == 1


@pytest.mark.unit
def test_recency_factor_decays_by_half_life():
    now = 1_000_000_000.0
    day = 86400.0
    assert tools._recency_factor(now, now) == pytest.approx(1.0)
    half = tools._recency_factor(now - tools._RECENCY_HALFLIFE_DAYS * day, now)
    assert half == pytest.approx(0.5, abs=1e-6)
    ancient = tools._recency_factor(now - 1095 * day, now)  # ~3 years
    assert ancient < 0.02
    assert tools._recency_factor(None, now) == 0.0  # unknown age -> neutral


@pytest.mark.unit
def test_rerank_prefers_recent_when_otherwise_tied():
    import time

    now = time.time()
    idx = {
        "docs": [
            {"key": "OLD", "summary": "okta", "source": "jira", "updated_ts": now - 1095 * 86400},
            {"key": "NEW", "summary": "okta", "source": "jira", "updated_ts": now - 1 * 86400},
        ]
    }
    # Equal base + equal lexical; recency must put the fresh ticket first.
    order = tools._feature_rerank("okta", [(0, 0.01), (1, 0.01)], idx)
    assert order[0] == 1


@pytest.mark.unit
def test_rerank_does_not_overturn_clear_leader():
    # The 0.045 vs 0.032 gap mirrors the real bug: a keyword-#1 exact PR match
    # (high fused) vs a vec-ranked doc ~28% lower. The original additive boost
    # (+0.05 lexical) flipped these; the multiplicative boost must not. doc 1
    # here maxes out lexical + corroboration and still must stay below doc 0.
    idx = {
        "docs": [
            {"key": "EXACT", "summary": "no overlap here", "source": "github", "has_links": True},
            {"key": "LEXY", "summary": "payment gateway", "source": "github", "has_links": True},
        ]
    }
    # doc 0 leads fusion clearly; doc 1 maxes out lexical + corroboration.
    order = tools._feature_rerank("payment gateway", [(0, 0.045), (1, 0.032)], idx)
    assert order[0] == 0


@pytest.mark.unit
def test_index_is_isolated_per_tenant(isolated_index, monkeypatch):
    # The agent is process-global; the index must NOT be shared across tenants,
    # or one tenant's tickets leak into another's search.
    from agent_yoku.storage import tenancy

    monkeypatch.setattr(
        tools,
        "_load_index",
        lambda: {"docs": [{"key": tenancy.current_tenant()}], "inverted": {}, "idf": {}},
    )

    tenancy.set_tenant("tenant-a")
    a = tools._index()
    tenancy.set_tenant("tenant-b")
    b = tools._index()

    assert a["docs"][0]["key"] == "tenant-a"
    assert b["docs"][0]["key"] == "tenant-b"
    tenancy.set_tenant("tenant-a")
    assert tools._index() is a, "tenant A's index should be cached, not rebuilt as B's"


@pytest.mark.unit
def test_invalidate_index_forces_rebuild(isolated_index, monkeypatch):
    builds = {"n": 0}

    def fake_load():
        builds["n"] += 1
        return {"docs": [], "inverted": {}, "idf": {}}

    monkeypatch.setattr(tools, "_load_index", fake_load)

    tools._index()
    tools._index()
    assert builds["n"] == 1, "second call should hit the cache"

    tools.invalidate_index()
    tools._index()
    assert builds["n"] == 2, "invalidation should force a rebuild"


@pytest.mark.unit
def test_embed_query_is_cached(monkeypatch):
    calls = {"n": 0}

    class _FakeClient:
        class embeddings:
            @staticmethod
            def create(model, input):
                calls["n"] += 1
                return type("R", (), {"data": [type("D", (), {"embedding": [1.0, 0.0, 0.0]})()]})

    monkeypatch.setattr(tools, "openai_client", lambda: _FakeClient())
    tools._embed_query.cache_clear()

    a = tools._embed_query("same query")
    b = tools._embed_query("same query")
    assert calls["n"] == 1, "repeated query should hit the cache, not re-embed"
    assert np.allclose(a, b)
    tools._embed_query.cache_clear()


@pytest.mark.unit
def test_rerank_corroboration_changes_search_order(isolated_index, monkeypatch):
    # Two otherwise-identical tickets; only AS-2 has linked PRs. Fusion alone
    # would tie them (AS-1 marginally ahead by insertion order); the reranker's
    # corroboration boost must lift AS-2 to the top.
    jira = [
        {"key": "AS-1", "summary": "shared topic", "embedding": [1.0, 0.0, 0.0]},
        {
            "key": "AS-2",
            "summary": "shared topic",
            "embedding": [1.0, 0.0, 0.0],
            "linked_prs": [{"key": "AsatoCorp/r#1"}],
        },
    ]
    _seed_index(monkeypatch, jira=jira, query_vec=(1.0, 0.0, 0.0))

    out = tools.semantic_search.invoke({"query": "shared topic", "k": 5})
    assert out[0]["key"] == "AS-2"
