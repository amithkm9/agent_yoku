"""Tests for the Atlassian Document Format → plain text flattener."""

from __future__ import annotations

from yoku.pipeline.connectors.jira.client import adf_to_text


def test_none_returns_empty():
    assert adf_to_text(None) == ""


def test_string_passthrough():
    assert adf_to_text("hello") == "hello"


def test_extracts_text_leaves():
    node = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "world"},
                ],
            },
        ],
    }
    out = adf_to_text(node)
    assert "Hello " in out
    assert "world" in out


def test_handles_list_of_nodes():
    node = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    out = adf_to_text(node)
    assert "a" in out and "b" in out
