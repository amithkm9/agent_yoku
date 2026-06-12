"""Write-back action layer (M8) against a scratch mongo db.

External systems are never hit — connector clients and bindings are
monkeypatched. Pins the contract: propose never executes, execute requires
explicit approval + records the audit trail, rejection sticks, the agent
tool only proposes, and DM approval stays behind its flag.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def tenant(scratch_db):
    from yoku.db import tenancy

    tid = f"act_{uuid.uuid4().hex[:8]}"
    tenancy.set_tenant(tid)
    yield tid
    from pymongo import MongoClient

    from yoku.config import settings
    from yoku.db.tenancy import tenant_db_name

    MongoClient(settings.mongo_uri).drop_database(tenant_db_name(tid))
    tenancy.set_tenant(None)


@pytest.fixture
def stubbed_exec(monkeypatch):
    """Neutralize external surfaces: connector binding + sync_one + clients."""
    import contextlib

    from yoku.actions import executor

    monkeypatch.setattr(executor, "_bound_connector", lambda name: contextlib.nullcontext())
    monkeypatch.setattr("yoku.pipeline.sync_one.sync_one", lambda key: True)
    return executor


@pytest.mark.integration
def test_propose_validates_and_executes_nothing(tenant, stubbed_exec, monkeypatch):
    from yoku.db.mongo import action_log_collection

    called: list = []
    monkeypatch.setattr(
        "yoku.connectors.jira.client.transition_issue",
        lambda key, to: called.append((key, to)),
    )

    row = stubbed_exec.propose(
        "transition_ticket", "AS-1", {"to_status": "Done"}, proposed_by="agent", source="agent"
    )
    assert row["status"] == "proposed"
    assert called == []  # nothing ran
    assert action_log_collection().count_documents({"status": "proposed"}) == 1

    with pytest.raises(ValueError, match="missing required"):
        stubbed_exec.propose("transition_ticket", "AS-1", {}, proposed_by="x", source="api")
    with pytest.raises(ValueError, match="not a JIRA key"):
        stubbed_exec.propose(
            "transition_ticket", "nonsense", {"to_status": "Done"}, proposed_by="x", source="api"
        )
    with pytest.raises(ValueError, match="unknown action type"):
        stubbed_exec.propose("rm_rf", "AS-1", {}, proposed_by="x", source="api")


@pytest.mark.integration
def test_execute_runs_audits_and_syncs(tenant, stubbed_exec, monkeypatch):
    synced: list = []
    monkeypatch.setattr("yoku.pipeline.sync_one.sync_one", lambda key: synced.append(key) or True)
    monkeypatch.setattr(
        "yoku.connectors.jira.client.transition_issue",
        lambda key, to: {"key": key, "transitioned_to": to},
    )

    row = stubbed_exec.propose(
        "transition_ticket", "jira/AS-1", {"to_status": "Done"}, proposed_by="u1", source="api"
    )
    done = stubbed_exec.execute(row["action_id"], approved_by="u_admin")
    assert done["status"] == "executed"
    assert done["approved_by"] == "u_admin"
    assert done["result"]["transitioned_to"] == "Done"
    assert synced == ["jira/AS-1"]

    # Double-execution is refused — the gate is one-shot.
    with pytest.raises(ValueError, match="not proposed"):
        stubbed_exec.execute(row["action_id"], approved_by="u_admin")


@pytest.mark.integration
def test_failed_execution_lands_on_audit_row(tenant, stubbed_exec, monkeypatch):
    from yoku.db.mongo import action_log_collection

    def boom(key, to):
        raise RuntimeError("jira 403")

    monkeypatch.setattr("yoku.connectors.jira.client.transition_issue", boom)
    row = stubbed_exec.propose(
        "transition_ticket", "AS-2", {"to_status": "Done"}, proposed_by="u1", source="api"
    )
    with pytest.raises(RuntimeError):
        stubbed_exec.execute(row["action_id"], approved_by="u_admin")
    audit = action_log_collection().find_one({"action_id": row["action_id"]})
    assert audit["status"] == "failed" and "jira 403" in audit["error"]


@pytest.mark.integration
def test_link_pr_updates_local_links(tenant, stubbed_exec, monkeypatch):
    from yoku.db.mongo import dc_github_collection, dc_jira_collection

    dc_jira_collection().insert_one({"key": "AS-3", "linked_prs": []})
    dc_github_collection().insert_one(
        {"key": "o/r#7", "jira_keys": [], "repo": "o/r", "summary": "fix", "merged": True}
    )
    monkeypatch.setattr(
        "yoku.connectors.jira.client.add_comment", lambda key, body: {"comment_id": "c1"}
    )

    row = stubbed_exec.propose(
        "link_pr_to_ticket", "AS-3", {"pr_key": "o/r#7"}, proposed_by="u1", source="api"
    )
    stubbed_exec.execute(row["action_id"], approved_by="u_admin")

    assert dc_github_collection().find_one({"key": "o/r#7"})["jira_keys"] == ["AS-3"]
    linked = dc_jira_collection().find_one({"key": "AS-3"})["linked_prs"]
    assert linked[0]["key"] == "o/r#7" and linked[0]["merged"] is True


@pytest.mark.integration
def test_agent_tool_only_proposes(tenant):
    from yoku.agent.tools.propose_action import propose_action
    from yoku.db.mongo import action_log_collection

    out = propose_action.invoke(
        {
            "action_type": "comment_on_jira",
            "target_key": "AS-9",
            "payload": {"body": "linking the PR shortly"},
        }
    )
    assert out["proposed"] is True and "approval" in out["note"]
    assert action_log_collection().find_one({})["status"] == "proposed"

    bad = propose_action.invoke({"action_type": "nuke", "target_key": "", "payload": {}})
    assert "error" in bad and "known_types" in bad


@pytest.mark.integration
def test_actions_api_approve_reject_flow(tenant, stubbed_exec, monkeypatch):
    from fastapi.testclient import TestClient

    from yoku.db import tenancy
    from yoku.main import app

    client = TestClient(app)
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": "admin"},
        json={"email": "a@example.com", "password": "pw-strong-123"},
    )
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    tenancy.set_tenant(tenant)

    monkeypatch.setattr(
        "yoku.connectors.jira.client.add_comment", lambda key, body: {"comment_id": "c9"}
    )
    row1 = stubbed_exec.propose(
        "comment_on_jira", "AS-5", {"body": "hello"}, proposed_by="agent", source="agent"
    )
    row2 = stubbed_exec.propose(
        "comment_on_jira", "AS-6", {"body": "world"}, proposed_by="agent", source="agent"
    )

    body = client.get("/api/actions", headers=auth).json()
    assert body["total_proposed"] == 2

    r = client.post(f"/api/actions/{row1['action_id']}/approve", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "executed"
    r = client.post(f"/api/actions/{row2['action_id']}/reject", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert client.post("/api/actions/nope/approve", headers=auth).status_code == 404


@pytest.mark.integration
def test_dm_approval_gated_off_by_default(tenant, stubbed_exec, monkeypatch):
    from datetime import UTC, datetime

    from yoku.db.mongo import action_log_collection, conversations_collection
    from yoku.proactive.orchestrator import _maybe_approve_pending_action

    row = stubbed_exec.propose(
        "transition_ticket",
        "AS-7",
        {"to_status": "Done"},
        proposed_by="agent",
        source="agent",
        signal_id="sig1",
    )
    convo = {"signal_id": "sig1", "person_user_id": "u_p"}
    conversations_collection().insert_one(
        {**convo, "state": "awaiting_reply", "opened_at": datetime.now(UTC)}
    )

    # Flag off (default): even a clean "yes" executes nothing.
    _maybe_approve_pending_action(convo, "yes")
    assert action_log_collection().find_one({})["status"] == "proposed"

    # Flag on: affirmative from the asked person executes the one pending action.
    from yoku.config import settings

    monkeypatch.setattr(settings, "dm_approval_enabled", True)
    monkeypatch.setattr(
        "yoku.connectors.jira.client.transition_issue",
        lambda key, to: {"key": key, "transitioned_to": to},
    )
    _maybe_approve_pending_action(convo, "Close it!")
    audit = action_log_collection().find_one({"action_id": row["action_id"]})
    assert audit["status"] == "executed" and audit["approved_by"] == "dm:u_p"

    # Ambiguous replies never execute.
    row2 = stubbed_exec.propose(
        "transition_ticket",
        "AS-8",
        {"to_status": "Done"},
        proposed_by="agent",
        source="agent",
        signal_id="sig2",
    )
    _maybe_approve_pending_action({"signal_id": "sig2", "person_user_id": "u_p"}, "hmm maybe later")
    assert (
        action_log_collection().find_one({"action_id": row2["action_id"]})["status"] == "proposed"
    )
