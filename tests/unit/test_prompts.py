"""SYSTEM_PROMPT constant has the expected content."""

from __future__ import annotations

import pytest

from yoku.agent.prompts import SYSTEM_PROMPT


@pytest.mark.unit
def test_system_prompt_non_empty():
    assert SYSTEM_PROMPT.strip()


@pytest.mark.unit
def test_system_prompt_has_expected_content():
    assert SYSTEM_PROMPT.startswith("You are the yoku research orchestrator.")
    assert "## Tools" in SYSTEM_PROMPT
    assert "semantic_search" in SYSTEM_PROMPT
    assert "mongo_query" in SYSTEM_PROMPT
