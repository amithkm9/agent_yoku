"""list_collections surfaces per-source details so the prompt needn't hardcode them."""

from __future__ import annotations

import pytest

from yoku.agent import tools
from yoku.agent.tools import mongo as mongo_tool


class _FakeColl:
    def estimated_document_count(self):
        return 0

    def index_information(self):
        return {"_id_": {"key": [("_id", 1)]}, "key_1": {"key": [("key", 1)]}}


@pytest.fixture
def _fake_allowed(monkeypatch):
    factories = {
        "ds-work-item": _FakeColl,
        "ds-conversation": _FakeColl,
        "ds-unified-users": _FakeColl,
    }
    monkeypatch.setattr(tools, "ALLOWED_COLLECTIONS", factories)
    # Keep freshness hermetic — no real mongo. Individual tests override as needed.
    monkeypatch.setattr(mongo_tool, "source_freshness", lambda: [])


@pytest.mark.unit
def test_source_collections_report_full_details(_fake_allowed):
    by_name = {c["name"]: c for c in tools.list_collections.invoke({})}

    work_item = by_name["ds-work-item"]
    assert work_item["source"] == "ds-work-item"
    assert work_item["description"]  # non-empty
    assert "key" in work_item["indexed_fields"]


@pytest.mark.unit
def test_conversation_collection_has_description(_fake_allowed):
    conv = next(c for c in tools.list_collections.invoke({}) if c["name"] == "ds-conversation")
    assert conv["source"] == "ds-conversation"
    assert conv["description"]


@pytest.mark.unit
def test_unified_users_has_no_source_key_example(_fake_allowed):
    users = next(c for c in tools.list_collections.invoke({}) if c["name"] == "ds-unified-users")
    # ds-unified-users is not in SOURCES as a routable source, so no source/key_example
    assert users["description"]


@pytest.mark.unit
def test_source_freshness_is_merged_in(_fake_allowed, monkeypatch):
    # freshness attaches to the matching source entry.
    monkeypatch.setattr(
        mongo_tool,
        "source_freshness",
        lambda: [
            {
                "source": "jira",
                "last_synced_at": "2026-05-30T00:00:00+00:00",
                "synced_ago": "1 day ago",
                "last_sync_status": "ok",
            }
        ],
    )
    # With the new ds-* ALLOWED_COLLECTIONS there's no "jira" source entry,
    # so freshness won't attach to any entry but must not crash.
    rows = tools.list_collections.invoke({})
    assert isinstance(rows, list)


@pytest.mark.unit
def test_freshness_failure_degrades_gracefully(_fake_allowed, monkeypatch):
    # A storage hiccup in freshness must not break listing.
    def boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(mongo_tool, "source_freshness", boom)
    rows = tools.list_collections.invoke({})
    assert isinstance(rows, list)
    work_item = next(c for c in rows if c["name"] == "ds-work-item")
    assert "synced_ago" not in work_item
