"""Verify the BaseConnector discovery enumerates the bundled connectors."""

from __future__ import annotations

from agent_yoku.connectors.base import list_connectors


def test_lists_jira_and_github():
    names = {c.get("name") for c in list_connectors()}
    assert "jira" in names
    assert "github" in names
