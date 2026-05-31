"""Tests for the `_clean` helper that prepares mongo docs for agent consumption."""

from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId

from yoku.agent.tools import _clean


def test_drops_underscore_id():
    out = _clean({"_id": ObjectId(), "key": "AS-1"})
    assert "_id" not in out
    assert out["key"] == "AS-1"


def test_drops_caller_named_fields():
    out = _clean(
        {"key": "AS-1", "embedding": [0.1, 0.2], "text": "..."}, drop=("embedding", "text")
    )
    assert set(out.keys()) == {"key"}


def test_bson_safe_recursion_on_datetime():
    dt = datetime(2026, 5, 25, tzinfo=UTC)
    out = _clean({"key": "AS-1", "_synced_at": dt, "linked_prs": [{"merged_at": dt}]})
    assert isinstance(out["_synced_at"], str)
    assert out["_synced_at"].startswith("2026-05-25")
    assert out["linked_prs"][0]["merged_at"].startswith("2026-05-25")


def test_bson_safe_recursion_on_objectid():
    oid = ObjectId()
    out = _clean({"key": "AS-1", "ref": {"nested": oid}})
    assert out["ref"]["nested"] == str(oid)


def test_pure_strings_pass_through():
    in_doc = {"key": "AS-1", "status": "To Do", "labels": ["a", "b"]}
    out = _clean(in_doc)
    assert out == in_doc
