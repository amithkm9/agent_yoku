"""who_knows: rank people behind a topic, merged across JIRA + GitHub identities."""

from __future__ import annotations

import types

import pytest

from yoku.agent import tools


def _stub_search(monkeypatch, cards):
    monkeypatch.setattr(tools, "semantic_search", types.SimpleNamespace(invoke=lambda _d: cards))


@pytest.mark.unit
def test_merges_jira_and_github_identities(fake_collections, monkeypatch):
    _stub_search(
        monkeypatch,
        cards=[
            {"key": "AS-1", "source": "jira", "assignee": "Akshay Reddy"},
            {"key": "AS-2", "source": "jira", "assignee": "Akshay Reddy"},
            {"key": "AsatoCorp/r#1", "source": "github"},
        ],
    )
    fake_collections["github_prs"].docs = [{"key": "AsatoCorp/r#1", "author": "internet-zero"}]
    fake_collections["unified_users"].docs = [
        {
            "email": "akshay@asato.ai",
            "jira": {"displayName": "Akshay Reddy"},
            "github": {"login": "internet-zero"},
            "is_bot": False,
        }
    ]

    people = tools.who_knows.invoke({"topic": "okta", "limit": 5})

    assert len(people) == 1, "the two identities should collapse into one person"
    p = people[0]
    assert p["email"] == "akshay@asato.ai"
    assert p["jira_items"] == 2
    assert p["github_items"] == 1
    assert p["total"] == 3
    assert set(p["sample_keys"]) == {"AS-1", "AS-2", "AsatoCorp/r#1"}


@pytest.mark.unit
def test_ranks_by_total_contributions(fake_collections, monkeypatch):
    _stub_search(
        monkeypatch,
        cards=[
            {"key": "AS-1", "source": "jira", "assignee": "Busy Person"},
            {"key": "AS-2", "source": "jira", "assignee": "Busy Person"},
            {"key": "AS-3", "source": "jira", "assignee": "Quiet Person"},
        ],
    )
    fake_collections["unified_users"].docs = []  # no unified match -> raw fallback

    people = tools.who_knows.invoke({"topic": "x", "limit": 5})

    assert [p["name"] for p in people] == ["Busy Person", "Quiet Person"]
    assert people[0]["total"] == 2


@pytest.mark.unit
def test_excludes_bots(fake_collections, monkeypatch):
    _stub_search(
        monkeypatch,
        cards=[{"key": "AsatoCorp/r#1", "source": "github"}],
    )
    fake_collections["github_prs"].docs = [{"key": "AsatoCorp/r#1", "author": "dependabot"}]
    fake_collections["unified_users"].docs = [
        {"email": None, "github": {"login": "dependabot"}, "is_bot": True}
    ]

    assert tools.who_knows.invoke({"topic": "deps", "limit": 5}) == []


@pytest.mark.unit
def test_empty_topic_raises(fake_collections, monkeypatch):
    _stub_search(monkeypatch, cards=[])
    with pytest.raises(ValueError):
        tools.who_knows.invoke({"topic": "  "})


@pytest.mark.unit
def test_recency_weighting_demotes_stale_contributor(fake_collections, monkeypatch):
    from datetime import UTC, datetime

    recent = datetime.now(UTC).isoformat()
    old = "2021-01-01T00:00:00+00:00"
    _stub_search(
        monkeypatch,
        cards=[
            {"key": "AS-1", "source": "jira", "assignee": "Stale Sam"},
            {"key": "AS-2", "source": "jira", "assignee": "Stale Sam"},
            {"key": "AS-3", "source": "jira", "assignee": "Stale Sam"},
            {"key": "AS-9", "source": "jira", "assignee": "Fresh Fran"},
        ],
    )
    fake_collections["jira_tickets"].docs = [
        {"key": "AS-1", "updated": old},
        {"key": "AS-2", "updated": old},
        {"key": "AS-3", "updated": old},
        {"key": "AS-9", "updated": recent},
    ]
    fake_collections["unified_users"].docs = []

    people = tools.who_knows.invoke({"topic": "okta", "limit": 5})

    # Fran's single fresh ticket outranks Sam's three ancient ones.
    assert people[0]["name"] == "Fresh Fran"
    sam = next(p for p in people if p["name"] == "Stale Sam")
    assert sam["total"] == 3  # raw counts are still reported honestly


@pytest.mark.unit
def test_limit_is_respected(fake_collections, monkeypatch):
    _stub_search(
        monkeypatch,
        cards=[{"key": f"AS-{i}", "source": "jira", "assignee": f"Person {i}"} for i in range(10)],
    )
    fake_collections["unified_users"].docs = []
    assert len(tools.who_knows.invoke({"topic": "x", "limit": 3})) == 3
