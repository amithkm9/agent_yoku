"""People API: the "What yoku knows" surface — list + per-person memory."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def client(scratch_db):
    from yoku.main import app

    return TestClient(app)


def _signup(client, tenant: str) -> str:
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": "alice"},
        json={"email": "alice@example.com", "password": "pw-strong-123"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drop(tenant: str) -> None:
    from pymongo import MongoClient

    from yoku.config import settings

    MongoClient(settings.mongo_uri).drop_database(f"{settings.mongo_db}_{tenant}")


def _seed(tenant: str) -> None:
    from yoku.db import tenancy

    tenancy.set_tenant(tenant)
    from yoku.db.mongo import (
        beliefs_collection,
        ds_unified_users_collection,
        signals_collection,
    )
    from yoku.proactive.episodes import record_episode

    ds_unified_users_collection().insert_one(
        {"user_id": "u_priya", "jira": {"displayName": "Priya Sharma"}, "email": "p@x.io"}
    )
    beliefs_collection().insert_one(
        {
            "user_id": "u_priya",
            "pattern": "done_no_pr",
            "kind": "normal_workflow",
            "claim": "spikes don't get PRs",
            "confidence": 0.72,
            "evidence_count": 3,
        }
    )
    signals_collection().insert_one(
        {
            "signal_id": uuid.uuid4().hex,
            "detector": "merged_no_ticket",
            "item_key": "github/o/r#9",
            "person_user_id": "u_priya",
            "status": "sent",
            "commitment": {"text": "link the ticket", "due": "2026-06-20", "followups": 0},
        }
    )
    record_episode(
        kind="sent",
        text="Hey Priya — AS-1 is Done with no PR.",
        person_user_id="u_priya",
        signal_id="s1",
        item_key="jira/AS-1",
        detector="done_no_pr",
        source="slack",
        outcome="sent",
    )


@pytest.mark.integration
def test_people_list_and_detail(client):
    from yoku.db import tenancy

    tenant = f"ppl_{uuid.uuid4().hex[:6]}"
    token = _signup(client, tenant)
    try:
        _seed(tenant)

        # List surfaces the person with belief + commitment counts.
        body = client.get("/api/people", headers=_auth(token)).json()
        person = next(p for p in body["people"] if p["user_id"] == "u_priya")
        assert person["name"] == "Priya Sharma"
        assert person["belief_count"] == 1
        assert person["open_commitments"] == 1
        assert person["last_interaction_at"] is not None

        # Detail returns beliefs, commitments, and the timeline.
        d = client.get("/api/people/u_priya", headers=_auth(token)).json()
        assert d["name"] == "Priya Sharma"
        assert d["beliefs"][0]["claim"] == "spikes don't get PRs"
        assert d["commitments"][0]["text"] == "link the ticket"
        assert d["commitments"][0]["status"] == "sent"
        assert any(e["kind"] == "sent" for e in d["episodes"])
    finally:
        _drop(tenant)
        tenancy.set_tenant(None)


@pytest.mark.integration
def test_unknown_person_is_404(client):
    from yoku.db import tenancy

    tenant = f"ppl_{uuid.uuid4().hex[:6]}"
    token = _signup(client, tenant)
    try:
        r = client.get("/api/people/nobody", headers=_auth(token))
        assert r.status_code == 404
    finally:
        _drop(tenant)
        tenancy.set_tenant(None)


@pytest.mark.integration
def test_people_requires_auth(client):
    assert client.get("/api/people").status_code == 401
