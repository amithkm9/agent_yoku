"""source_freshness: per-source counts + last sync time for the agent + UI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from yoku.db import connector_configs as cc
from yoku.db import freshness


class _FakeColl:
    def __init__(self, n):
        self._n = n

    def estimated_document_count(self):
        return self._n


def _patch(monkeypatch, jira_n, github_n, configs, slack_n=0):
    # Patch _SOURCE_COLLECTIONS directly since it's a module-level dict that captures
    # the original function references at import time.
    monkeypatch.setattr(
        freshness,
        "_SOURCE_COLLECTIONS",
        {
            "jira": lambda: _FakeColl(jira_n),
            "github": lambda: _FakeColl(github_n),
            "slack": lambda: _FakeColl(slack_n),
        },
    )
    monkeypatch.setattr(cc, "list_configs", lambda: configs)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (5, "just now"),
        (60, "1 minute ago"),
        (180, "3 minutes ago"),
        (3600, "1 hour ago"),
        (7200, "2 hours ago"),
        (3 * 86400, "3 days ago"),
    ],
)
def test_ago_humanizes(seconds, expected):
    assert freshness._ago(seconds) == expected


@pytest.mark.unit
def test_freshness_reports_count_and_recent_sync(monkeypatch):
    synced = datetime.now(UTC) - timedelta(hours=2)
    _patch(
        monkeypatch,
        jira_n=5478,
        github_n=22992,
        configs=[
            {"name": "jira", "last_synced_at": synced, "last_sync_status": "ok"},
            {"name": "github", "last_synced_at": synced, "last_sync_status": "ok"},
        ],
    )
    rows = {r["source"]: r for r in freshness.source_freshness()}

    assert rows["jira"]["count"] == 5478
    assert rows["github"]["count"] == 22992
    assert rows["jira"]["synced_ago"] == "2 hours ago"
    assert rows["jira"]["last_sync_status"] == "ok"
    assert rows["jira"]["last_synced_at"] is not None


@pytest.mark.unit
def test_unconfigured_source_reports_never_but_keeps_count(monkeypatch):
    # GitHub has data (ingested out-of-band) but no connector config.
    _patch(
        monkeypatch,
        jira_n=10,
        github_n=500,
        configs=[{"name": "jira", "last_synced_at": datetime.now(UTC), "last_sync_status": "ok"}],
    )
    rows = {r["source"]: r for r in freshness.source_freshness()}

    assert rows["github"]["count"] == 500
    assert rows["github"]["last_synced_at"] is None
    assert rows["github"]["synced_ago"] == "never"
    assert rows["github"]["last_sync_status"] is None


@pytest.mark.unit
def test_naive_timestamp_is_treated_as_utc(monkeypatch):
    # Some drivers hand back tz-naive datetimes; must not raise on subtraction.
    naive = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    _patch(
        monkeypatch,
        jira_n=1,
        github_n=1,
        configs=[{"name": "jira", "last_synced_at": naive, "last_sync_status": "ok"}],
    )
    rows = {r["source"]: r for r in freshness.source_freshness()}

    assert rows["jira"]["synced_ago"] == "5 minutes ago"


@pytest.mark.unit
def test_slack_source_is_reported(monkeypatch):
    # Slack is an ingestable source and must appear in freshness, not be omitted.
    _patch(
        monkeypatch,
        jira_n=1,
        github_n=1,
        slack_n=42,
        configs=[{"name": "slack", "last_synced_at": datetime.now(UTC), "last_sync_status": "ok"}],
    )
    rows = {r["source"]: r for r in freshness.source_freshness()}

    assert rows["slack"]["count"] == 42
    assert rows["slack"]["last_sync_status"] == "ok"
