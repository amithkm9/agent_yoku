"""Tests for PR -> JIRA key extraction (the AS-XXXX linker)."""

from __future__ import annotations

from yoku.core.utils import extract_jira_keys


def test_extracts_from_branch():
    keys = extract_jira_keys("reddy/AS-4163-ltm-mongo", None, None)
    assert keys == ["AS-4163"]


def test_extracts_from_title_and_body():
    keys = extract_jira_keys(None, "[AS-1234] fix bug", "closes AS-1234, related to AS-5678")
    assert keys == ["AS-1234", "AS-5678"]


def test_dedupes_across_sources():
    keys = extract_jira_keys("reddy/AS-1", "[AS-1] x", "AS-1 again")
    assert keys == ["AS-1"]


def test_skips_lowercase_or_misformatted():
    keys = extract_jira_keys(None, "as-123 is not a match; AS123 either", None)
    assert keys == []


def test_handles_all_empty():
    assert extract_jira_keys(None, None, None) == []
