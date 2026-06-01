"""Relationship registry loads from YAML and answers directional queries."""

from __future__ import annotations

import pytest

from yoku.agent import relationships as rel


@pytest.mark.unit
def test_registry_loads_expected_relationships():
    names = {r.name for r in rel.all_relationships()}
    assert "github_pr_references_jira_ticket" in names
    assert "slack_message_references_jira_ticket" in names


@pytest.mark.unit
def test_outbound_from_github_prs_points_at_jira():
    outs = rel.outbound_relationships("dc-github")
    assert len(outs) == 1
    r = outs[0]
    assert r.entity2 == "dc-jira"
    assert r.join.local_field == "jira_keys"
    assert r.join.foreign_field == "key"


@pytest.mark.unit
def test_inbound_to_jira_tickets_from_both_sources():
    referrers = {r.entity1 for r in rel.inbound_relationships("dc-jira")}
    assert referrers == {"dc-github", "dc-slack"}


@pytest.mark.unit
def test_relationships_for_is_bidirectional():
    names = {r.name for r in rel.relationships_for("ds-unified-users")}
    assert names == {"jira_user_to_unified", "github_user_to_unified"}
