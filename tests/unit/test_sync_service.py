"""Unit tests for the tenant auto-sync orchestration.

All collaborators (connector configs, per-connector ingest, the post-ingest
pipeline, the mongo client) are monkeypatched, so these tests are hermetic — no
network, no mongo, no OpenAI.
"""

from __future__ import annotations

import pytest

from yoku.db import connector_configs as cc
from yoku.db import mongo, tenancy
from yoku.pipeline import sync_service


@pytest.fixture
def reset_tenant():
    yield
    tenancy.set_tenant(None)


# ---------- list_tenant_ids ----------


def test_list_tenant_ids_strips_prefix_and_filters(monkeypatch):
    class FakeClient:
        def list_database_names(self):
            return [
                "admin",
                "config",
                "local",
                "yoku",  # unsuffixed base db — not a tenant
                "yoku_acme",
                "yoku_globex",
                "other_db",
            ]

    monkeypatch.setattr(sync_service.settings, "mongo_db", "yoku")
    monkeypatch.setattr(mongo, "_client", lambda: FakeClient())

    assert sync_service.list_tenant_ids() == ["acme", "globex"]


# ---------- _ingest_connector ----------


def test_ingest_connector_skips_when_unconfigured(monkeypatch, reset_tenant):
    tenancy.set_tenant("acme")
    monkeypatch.setattr(cc, "get_config_decrypted", lambda name: None)
    marked: list = []
    monkeypatch.setattr(cc, "mark_synced", lambda *a, **k: marked.append((a, k)))

    assert sync_service._ingest_connector("jira") is False
    assert marked == []  # nothing attempted, nothing recorded


def test_ingest_connector_runs_and_marks_ok(monkeypatch, reset_tenant):
    tenancy.set_tenant("acme")
    calls: list[str] = []
    monkeypatch.setattr(cc, "get_config_decrypted", lambda name: {"token": "t"})
    monkeypatch.setattr(cc, "mark_synced", lambda name, *, ok, error=None: calls.append((name, ok)))
    monkeypatch.setattr(
        sync_service,
        "_INGEST_FNS",
        {"jira": lambda decrypted: calls.append("ingested")},
    )

    assert sync_service._ingest_connector("jira") is True
    assert "ingested" in calls
    assert ("jira", True) in calls


def test_ingest_connector_records_failure_without_raising(monkeypatch, reset_tenant):
    tenancy.set_tenant("acme")
    recorded: dict = {}

    def boom(_decrypted):
        raise RuntimeError("upstream 500")

    monkeypatch.setattr(cc, "get_config_decrypted", lambda name: {"token": "t"})
    monkeypatch.setattr(sync_service, "_INGEST_FNS", {"jira": boom})
    monkeypatch.setattr(
        cc,
        "mark_synced",
        lambda name, *, ok, error=None: recorded.update(name=name, ok=ok, error=error),
    )

    # Must not raise — failure is swallowed and recorded.
    assert sync_service._ingest_connector("jira") is True
    assert recorded["ok"] is False
    assert "RuntimeError" in recorded["error"]


# ---------- run_full_pipeline_for_tenant ----------


def test_pipeline_skips_post_steps_when_no_connectors(monkeypatch, reset_tenant):
    monkeypatch.setattr(sync_service, "_ingest_connector", lambda name: False)
    post_ran: list = []
    monkeypatch.setattr(sync_service, "_run_post_ingest_pipeline", lambda: post_ran.append(True))
    monkeypatch.setattr(sync_service, "invalidate_index", lambda _t: post_ran.append("index"))

    result = sync_service.run_full_pipeline_for_tenant("acme")

    assert result == {"tenant": "acme", "connectors": [], "embedded": False}
    assert post_ran == []  # no embed/link/index when nothing was ingested


def test_pipeline_runs_post_steps_and_reindexes(monkeypatch, reset_tenant):
    ran = [name for name in cc.SUPPORTED_CONNECTORS]  # both configured
    monkeypatch.setattr(sync_service, "_ingest_connector", lambda name: name in ran)
    order: list = []
    monkeypatch.setattr(sync_service, "_run_post_ingest_pipeline", lambda: order.append("post"))
    monkeypatch.setattr(sync_service, "invalidate_index", lambda t: order.append(("index", t)))

    result = sync_service.run_full_pipeline_for_tenant("acme")

    assert result["embedded"] is True
    assert set(result["connectors"]) == set(cc.SUPPORTED_CONNECTORS)
    # Post-ingest pipeline runs before the index refresh.
    assert order == ["post", ("index", "acme")]


# ---------- _run_post_ingest_pipeline ----------


def test_post_ingest_pipeline_runs_unify_then_entity_links(monkeypatch):
    """Every post-ingest step runs; unify then entity_links close out the pipeline."""
    from yoku.db import backfill, unified_users
    from yoku.pipeline import embed as embed_mod
    from yoku.pipeline import entity_links, pr_to_jira, slack_threads, unify

    order: list[str] = []
    monkeypatch.setattr(slack_threads, "rollup_threads", lambda: order.append("slack_threads"))
    monkeypatch.setattr(embed_mod, "main", lambda: order.append("embed"))
    monkeypatch.setattr(unified_users, "main", lambda: order.append("unify_users"))
    monkeypatch.setattr(pr_to_jira, "main", lambda: order.append("link"))
    monkeypatch.setattr(backfill, "main", lambda: order.append("backfill"))
    monkeypatch.setattr(unify, "unify_all", lambda: order.append("unify_docs"))
    monkeypatch.setattr(entity_links, "build_entity_links", lambda: order.append("entity_links"))

    sync_service._run_post_ingest_pipeline()

    # Thread rollup precedes embed so re-rolled parents re-embed in this pass.
    assert order == [
        "slack_threads",
        "embed",
        "unify_users",
        "link",
        "backfill",
        "unify_docs",
        "entity_links",
    ]


def test_post_ingest_projection_failure_is_best_effort(monkeypatch, reset_tenant):
    """A failing canonical-projection step is logged, not raised, and the next runs."""
    tenancy.set_tenant("acme")
    from yoku.db import backfill, unified_users
    from yoku.pipeline import embed as embed_mod
    from yoku.pipeline import entity_links, pr_to_jira, slack_threads, unify

    ran: list[str] = []
    monkeypatch.setattr(slack_threads, "rollup_threads", lambda: ran.append("slack_threads"))
    monkeypatch.setattr(embed_mod, "main", lambda: ran.append("embed"))
    monkeypatch.setattr(unified_users, "main", lambda: ran.append("unify_users"))
    monkeypatch.setattr(pr_to_jira, "main", lambda: ran.append("link"))
    monkeypatch.setattr(backfill, "main", lambda: ran.append("backfill"))

    def boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(unify, "unify_all", boom)
    monkeypatch.setattr(entity_links, "build_entity_links", lambda: ran.append("entity_links"))

    # Must not raise even though unify blew up; entity_links still runs.
    sync_service._run_post_ingest_pipeline()

    assert ran == ["slack_threads", "embed", "unify_users", "link", "backfill", "entity_links"]


# ---------- run_all_tenants ----------


def test_run_all_tenants_continues_past_a_crash(monkeypatch):
    monkeypatch.setattr(sync_service, "list_tenant_ids", lambda: ["a", "b", "c"])
    visited: list[str] = []

    def fake_pipeline(tenant_id: str):
        visited.append(tenant_id)
        if tenant_id == "b":
            raise RuntimeError("kaboom")

    monkeypatch.setattr(sync_service, "run_full_pipeline_for_tenant", fake_pipeline)

    sync_service.run_all_tenants()  # must not raise

    assert visited == ["a", "b", "c"]
