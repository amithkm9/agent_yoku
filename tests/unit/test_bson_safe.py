"""Tests for the BSON -> JSON-safe serializer."""

from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId

from yoku.core.utils import bson_safe


def test_passthrough_primitives():
    assert bson_safe(42) == 42
    assert bson_safe("hello") == "hello"
    assert bson_safe(None) is None
    assert bson_safe(True) is True


def test_objectid_becomes_string():
    oid = ObjectId()
    assert bson_safe(oid) == str(oid)


def test_datetime_iso_format():
    dt = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    assert bson_safe(dt) == dt.isoformat()


def test_nested_structure():
    oid = ObjectId()
    dt = datetime(2026, 5, 25, tzinfo=UTC)
    nested = {"a": [1, oid, {"d": dt}]}
    out = bson_safe(nested)
    assert out == {"a": [1, str(oid), {"d": dt.isoformat()}]}
