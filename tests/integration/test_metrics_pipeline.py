"""Trend metrics computation + trends API against a scratch mongo db."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)  # a Thursday; week = 2026-06-08


def _iso(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _series(rows, metric):
    return {r["week"]: r for r in rows if r["metric"] == metric}


@pytest.mark.integration
def test_compute_metrics_counts_and_cycle_time(tenant):
    from yoku.db.mongo import dc_github_collection, dc_jira_collection, ds_metrics_collection
    from yoku.pipeline.metrics import compute_metrics

    dc_github_collection().insert_many(
        [
            # opened Tue this week, merged this week — 2-day cycle
            {"key": "o/r#1", "created": _iso(2), "merged_at": _iso(0)},
            # opened Sun LAST week, merged this week — 4-day cycle
            {"key": "o/r#2", "created": _iso(4), "merged_at": _iso(0)},
            # opened last week, still open
            {"key": "o/r#3", "created": _iso(8), "merged_at": None},
        ]
    )
    dc_jira_collection().insert_one({"key": "AS-1", "created": _iso(1)})

    compute_metrics(weeks=4, now=_NOW)
    rows = list(ds_metrics_collection().find({}, {"_id": 0}))

    this_week = "2026-06-08"
    last_week = "2026-06-01"
    assert _series(rows, "prs_merged")[this_week]["value"] == 2
    assert _series(rows, "prs_opened")[last_week]["value"] == 2
    assert _series(rows, "prs_opened")[this_week]["value"] == 1
    assert _series(rows, "tickets_created")[this_week]["value"] == 1
    # zero-filled weeks exist for count metrics (continuous charts)
    assert _series(rows, "prs_merged")[last_week]["value"] == 0

    cycle = _series(rows, "pr_cycle_time_days")[this_week]
    assert cycle["value"] == 3.0  # median of 2 and 4 days
    assert cycle["n"] == 2

    p90 = _series(rows, "pr_cycle_time_p90_days")[this_week]
    assert p90["value"] > cycle["value"]  # tail sits above the median
    assert p90["n"] == 2


@pytest.mark.integration
def test_tickets_done_comes_from_events_and_recompute_is_idempotent(tenant):
    from yoku.db.mongo import ds_metrics_collection, events_collection
    from yoku.pipeline.metrics import compute_metrics

    events_collection().insert_many(
        [
            {"kind": "updated", "field": "status", "new": "Done", "ts": _NOW},
            {"kind": "updated", "field": "status", "new": "Closed", "ts": _NOW},
            {"kind": "updated", "field": "status", "new": "In Progress", "ts": _NOW},  # not done
            {"kind": "updated", "field": "assignee", "new": "Done", "ts": _NOW},  # wrong field
        ]
    )

    compute_metrics(weeks=4, now=_NOW)
    compute_metrics(weeks=4, now=_NOW)  # idempotent — no duplicate rows

    rows = list(ds_metrics_collection().find({"metric": "tickets_done"}, {"_id": 0}))
    assert len(rows) == 4  # one row per week in the window, no dupes
    assert _series(rows, "tickets_done")["2026-06-08"]["value"] == 2


@pytest.mark.integration
def test_trends_api_returns_series(tenant):
    from yoku.db import tenancy
    from yoku.main import app
    from yoku.pipeline.metrics import compute_metrics

    client = TestClient(app)
    suffix = uuid.uuid4().hex[:6]
    r = client.post(
        "/api/auth/signup",
        params={"tenant": tenant, "name": "t"},
        json={"email": f"t{suffix}@example.com", "password": "pw-strong-123"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    tenancy.set_tenant(tenant)  # signup rebinds; restore for compute

    from yoku.db.mongo import dc_github_collection

    dc_github_collection().insert_one({"key": "o/r#1", "created": _iso(2), "merged_at": _iso(0)})
    compute_metrics(weeks=4, now=_NOW)

    r = client.get("/api/stats/trends?weeks=4", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "2026-06-08" in body["weeks"]
    merged = {p["week"]: p["value"] for p in body["series"]["prs_merged"]}
    assert merged["2026-06-08"] == 1
    assert "signals_open" in body["series"]
