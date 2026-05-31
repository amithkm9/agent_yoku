"""Source registry routing: key-shape detection plus get / linked over it."""

from __future__ import annotations

import pytest

from yoku.agent import relationships, sources, tools


@pytest.mark.unit
@pytest.mark.parametrize(
    "key, expected",
    [
        ("AS-4163", "jira"),
        ("ENG-12", "jira"),
        ("AsatoCorp/agent-svc#173", "github"),
        ("C0AB123/1700000000.123456", "slack"),
        ("not a key", None),
    ],
)
def test_source_for_key(key, expected):
    spec = sources.source_for_key(key)
    assert (spec.name if spec else None) == expected


@pytest.mark.unit
def test_jira_tickets_referenced_by_github_and_slack():
    referrers = {r.entity1 for r in relationships.inbound_relationships("jira_tickets")}
    assert referrers == {"github_prs", "slack_messages"}


class _FakeColl:
    def __init__(self, docs):
        self._docs = list(docs)

    def find_one(self, q=None, proj=None):
        for d in self._docs:
            if d.get("key") == (q or {}).get("key"):
                return d
        return None

    def find(self, q=None, proj=None):
        return _FakeCursor([d for d in self._docs if self._matches(d, q or {})])

    @staticmethod
    def _matches(doc, q):
        for k, v in q.items():
            cur = doc.get(k)
            if isinstance(v, dict) and "$in" in v:
                if cur not in v["$in"]:
                    return False
            # scalar equality, or a scalar query value matching a list field
            elif cur != v and not (isinstance(cur, list) and v in cur):
                return False
        return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, _n):
        return self

    def __iter__(self):
        return iter(self._docs)


@pytest.fixture
def routed_collections(monkeypatch):
    colls = {
        "jira_tickets": _FakeColl([{"key": "AS-1", "summary": "ticket one", "status": "Done"}]),
        "github_prs": _FakeColl(
            [{"key": "AsatoCorp/r#1", "summary": "pr one", "jira_keys": ["AS-1"]}]
        ),
        "slack_messages": _FakeColl(
            [{"key": "C1/1.0", "summary": "chatter", "jira_keys": ["AS-1"]}]
        ),
    }
    monkeypatch.setattr(tools, "get_collection", lambda name: colls[name])
    return colls


@pytest.mark.unit
def test_get_routes_by_key_shape(routed_collections):
    assert tools.get.invoke({"key": "AS-1"})["summary"] == "ticket one"
    assert tools.get.invoke({"key": "AsatoCorp/r#1"})["summary"] == "pr one"
    assert tools.get.invoke({"key": "C1/1.0"})["summary"] == "chatter"


@pytest.mark.unit
def test_get_unknown_key_shape_raises(routed_collections):
    from langchain_core.tools import ToolException

    with pytest.raises(ToolException, match="unrecognised key shape"):
        tools.get.invoke({"key": "garbage"})


@pytest.mark.unit
def test_linked_outbound_pr_to_tickets(routed_collections):
    out = tools.linked.invoke({"key": "AsatoCorp/r#1"})
    assert [(r["key"], r["source"]) for r in out] == [("AS-1", "jira")]


@pytest.mark.unit
def test_linked_inbound_ticket_to_referrers(routed_collections):
    out = tools.linked.invoke({"key": "AS-1"})
    by_source = {r["source"]: r["key"] for r in out}
    assert by_source == {"github": "AsatoCorp/r#1", "slack": "C1/1.0"}
