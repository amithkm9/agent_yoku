"""Inbox API: list matured signals, confirm/dismiss verdicts."""

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


@pytest.mark.integration
def test_inbox_lists_only_matured_and_verdicts_stick(client):
    from datetime import UTC, datetime, timedelta

    from yoku.db import tenancy

    tenant = f"inbox_{uuid.uuid4().hex[:6]}"
    token = _signup(client, tenant)
    try:
        tenancy.set_tenant(tenant)
        from yoku.db.mongo import dc_jira_collection
        from yoku.proactive.signals import run_detectors

        old = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        fresh = datetime.now(UTC).isoformat()
        dc_jira_collection().insert_many(
            [
                {
                    "key": "AS-1",
                    "summary": "old gap",
                    "status": "Done",
                    "assignee": None,
                    "linked_prs": [],
                    "updated": old,
                },
                {
                    "key": "AS-2",
                    "summary": "fresh gap",
                    "status": "Done",
                    "assignee": None,
                    "linked_prs": [],
                    "updated": fresh,
                },
            ]
        )
        run_detectors()

        r = client.get("/api/inbox", headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_open"] == 2
        assert body["total_matured"] == 1  # only the old gap is Inbox-visible
        assert [s["item_key"] for s in body["signals"]] == ["jira/AS-1"]

        sid = body["signals"][0]["signal_id"]
        r = client.post(f"/api/inbox/{sid}/confirm", headers=_auth(token))
        assert r.status_code == 200 and r.json()["label"] == "confirmed"
        # Confirmed signals stay in the inbox (still actionable).
        assert len(client.get("/api/inbox", headers=_auth(token)).json()["signals"]) == 1

        r = client.post(f"/api/inbox/{sid}/dismiss", headers=_auth(token))
        assert r.status_code == 200 and r.json()["status"] == "dismissed"
        assert client.get("/api/inbox", headers=_auth(token)).json()["signals"] == []

        r = client.post("/api/inbox/nope/dismiss", headers=_auth(token))
        assert r.status_code == 404
    finally:
        _drop(tenant)
        tenancy.set_tenant(None)


@pytest.mark.integration
def test_inbox_requires_auth(client):
    assert client.get("/api/inbox").status_code == 401
