"""Tool auto-discovery returns the expected, stable toolkit."""

from __future__ import annotations

import pytest

from yoku.agent import tools
from yoku.agent.tools._registry import discover_tools

_EXPECTED = {
    "describe_collection",
    "list_collections",
    "mongo_count",
    "mongo_query",
    "resolve_user",
    "semantic_search",
    "who_knows",
}


@pytest.mark.unit
def test_discover_tools_returns_expected_set():
    names = {t.name for t in discover_tools()}
    assert names == _EXPECTED


@pytest.mark.unit
def test_all_tools_matches_discovery():
    assert [t.name for t in tools.ALL_TOOLS] == [t.name for t in tools.get_all_tools()]
    assert {t.name for t in tools.ALL_TOOLS} == _EXPECTED


@pytest.mark.unit
def test_discovery_order_is_stable_and_sorted():
    names = [t.name for t in discover_tools()]
    assert names == sorted(names)


@pytest.mark.unit
def test_discovered_objects_are_langchain_tools():
    from langchain_core.tools import BaseTool

    assert all(isinstance(t, BaseTool) for t in tools.ALL_TOOLS)
