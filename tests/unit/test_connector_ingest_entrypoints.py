from __future__ import annotations

from datetime import UTC, datetime

from yoku.connectors._runtime import GithubConfig, JiraConfig, use_github, use_jira
from yoku.connectors.github import ingest as gh_ingest
from yoku.connectors.jira import ingest as jira_ingest


class _FakeCollection:
    def find_one(self, *_args, **_kwargs):
        return None

    def bulk_write(self, *_args, **_kwargs):
        return None


def test_jira_main_ignores_process_argv(monkeypatch):
    seen: dict[str, str] = {}

    def fake_search_issues(jql: str, page_size: int = 100):
        seen["jql"] = jql
        seen["page_size"] = str(page_size)
        return iter(())

    monkeypatch.setattr(jira_ingest, "tickets_collection", lambda: _FakeCollection())
    monkeypatch.setattr(jira_ingest, "search_issues", fake_search_issues)

    cfg = JiraConfig(
        base_url="https://acme.atlassian.net",
        email="ops@acme.com",
        token="tok",
        project="AC",
    )
    with use_jira(cfg):
        jira_ingest.main()

    assert seen["jql"] == 'project = "AC" ORDER BY updated DESC'


def test_github_main_ignores_process_argv(monkeypatch):
    seen: dict[str, object] = {}

    def fake_lookback_cutoff():
        return datetime(2026, 1, 1, tzinfo=UTC)

    def fake_repos_to_scan(filter_names: list[str]):
        seen["filters"] = filter_names
        return []

    monkeypatch.setattr(gh_ingest, "github_prs_collection", lambda: _FakeCollection())
    monkeypatch.setattr(gh_ingest, "lookback_cutoff", fake_lookback_cutoff)
    monkeypatch.setattr(gh_ingest, "_repos_to_scan", fake_repos_to_scan)

    cfg = GithubConfig(
        api_base="https://api.github.com",
        token="tok",
        org="acme",
        pr_lookback_days=30,
    )
    with use_github(cfg):
        gh_ingest.main()

    assert seen["filters"] == []
