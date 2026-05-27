"""Cross-source consistency report."""

from __future__ import annotations

import pytest

from agent_yoku.analysis import consistency


@pytest.mark.unit
def test_flags_done_tickets_without_prs_and_merged_prs_without_tickets(
    fake_collections, monkeypatch
):
    monkeypatch.setattr(consistency, "tickets_collection", lambda: fake_collections["jira_tickets"])
    monkeypatch.setattr(
        consistency, "github_prs_collection", lambda: fake_collections["github_prs"]
    )

    fake_collections["jira_tickets"].docs = [
        {"key": "AS-1", "status": "Done", "linked_prs": []},  # flagged
        {"key": "AS-2", "status": "Done", "linked_prs": [{"key": "r#1"}]},  # has code
        {"key": "AS-3", "status": "In Progress", "linked_prs": []},  # not done -> ignore
        {"key": "AS-4", "status": "Closed"},  # missing field -> flagged
    ]
    fake_collections["github_prs"].docs = [
        {"key": "r#1", "status": "merged", "jira_keys": []},  # flagged
        {"key": "r#2", "status": "merged", "jira_keys": ["AS-9"]},  # linked
        {"key": "r#3", "status": "open", "jira_keys": []},  # not merged -> ignore
    ]

    report = consistency.consistency_report(sample=10)

    assert report["done_without_pr"]["count"] == 2
    assert set(report["done_without_pr"]["sample"]) == {"AS-1", "AS-4"}
    assert report["merged_without_ticket"]["count"] == 1
    assert report["merged_without_ticket"]["sample"] == ["r#1"]


@pytest.mark.unit
def test_sample_is_capped(fake_collections, monkeypatch):
    monkeypatch.setattr(consistency, "tickets_collection", lambda: fake_collections["jira_tickets"])
    monkeypatch.setattr(
        consistency, "github_prs_collection", lambda: fake_collections["github_prs"]
    )
    fake_collections["jira_tickets"].docs = [
        {"key": f"AS-{i}", "status": "Done", "linked_prs": []} for i in range(20)
    ]

    report = consistency.consistency_report(sample=5)

    assert report["done_without_pr"]["count"] == 20
    assert len(report["done_without_pr"]["sample"]) == 5
