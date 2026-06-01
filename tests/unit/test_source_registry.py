"""Source registry routing: key-shape detection and cross-source relationships."""

from __future__ import annotations

import pytest

from yoku.agent import sources
from yoku.schemas import relationships


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
    """Namespaced ds-* keys must never be misrouted to a per-source spec."""
    assert sources.source_for_key(key) is None


@pytest.mark.unit
def test_ds_sources_registered_but_non_routable_and_non_embeddable():
    for name in ("ds-work-item", "ds-pull-request", "ds-conversation"):
        spec = sources.get_source(name)
        assert spec is not None and spec.collection == name
        assert spec.routable is False
        assert spec not in sources.embeddable_sources()


@pytest.mark.unit
def test_jira_tickets_referenced_by_github_and_slack():
    referrers = {r.entity1 for r in relationships.inbound_relationships("dc-jira")}
    assert referrers == {"dc-github", "dc-slack"}
