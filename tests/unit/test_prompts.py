"""Prompt loader reads the versioned asset and returns the expected content."""

from __future__ import annotations

import pytest

from yoku.agent.prompts import load_prompt


@pytest.mark.unit
def test_load_main_prompt_non_empty():
    text = load_prompt("main")
    assert text.strip()


@pytest.mark.unit
def test_main_prompt_has_expected_content():
    text = load_prompt("main")
    assert text.startswith("You are the yoku research orchestrator.")
    assert "## Tool ladder" in text
    assert "semantic_search" in text


@pytest.mark.unit
def test_missing_prompt_raises():
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist")
