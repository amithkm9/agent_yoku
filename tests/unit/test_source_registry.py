"""Source registry routing: key-shape detection and cross-source relationships."""

from __future__ import annotations

import pytest

from yoku.agent import relationships, sources


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
@pytest.mark.parametrize(
    "key",
    [
        "github/AsatoCorp/agent-svc#173",  # would match GitHub's `#\d+$`
        "jira/AS-4163",
        "slack/C0AB123/1700000000.123456",  # would match Slack's `chan/ts`
    ],
)
def test_canonical_documents_keys_are_not_routed_to_a_per_source(key):
    """Namespaced `documents` keys must never be misrouted to a per-source spec."""
    assert sources.source_for_key(key) is None


@pytest.mark.unit
def test_documents_registered_but_non_routable_and_non_embeddable():
    docs = sources.get_source("documents")
    assert docs is not None and docs.collection == "documents"
    assert docs.routable is False
    assert docs not in sources.embeddable_sources()


@pytest.mark.unit
def test_jira_tickets_referenced_by_github_and_slack():
    referrers = {r.entity1 for r in relationships.inbound_relationships("jira_tickets")}
    assert referrers == {"github_prs", "slack_messages"}
