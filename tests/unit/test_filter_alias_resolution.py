"""Verify filter(source, …) translates person aliases via unified_users and maps
each source's fields onto the right mongo query."""

from __future__ import annotations

from yoku.agent import tools


def test_filter_jira_translates_github_login_to_jira_name(fake_collections):
    fake_collections["unified_users"].docs = [
        {"jira": {"displayName": "Akshay Reddy"}, "github": {"login": "internet-zero"}},
    ]
    tools.filter.invoke({"source": "jira", "filters": {"assignee": "internet-zero"}, "limit": 5})

    last_find = fake_collections["jira_tickets"].find_calls[-1][0]
    assert last_find["assignee"] == "Akshay Reddy"


def test_filter_jira_passes_through_when_no_alias(fake_collections):
    fake_collections["unified_users"].docs = []
    tools.filter.invoke({"source": "jira", "filters": {"assignee": "Some Random"}, "limit": 5})

    last_find = fake_collections["jira_tickets"].find_calls[-1][0]
    assert last_find["assignee"] == "Some Random"


def test_filter_prs_translates_jira_displayname_to_gh_login(fake_collections):
    fake_collections["unified_users"].docs = [
        {"jira": {"displayName": "Akshay Reddy"}, "github": {"login": "internet-zero"}},
    ]
    tools.filter.invoke({"source": "github", "filters": {"author": "Akshay Reddy"}, "limit": 5})

    last_find = fake_collections["github_prs"].find_calls[-1][0]
    assert last_find["author"] == "internet-zero"


def test_filter_jira_applies_fix_version(fake_collections):
    tools.filter.invoke(
        {"source": "jira", "filters": {"fix_version": "release-05-26-2026"}, "limit": 5}
    )
    last_find = fake_collections["jira_tickets"].find_calls[-1][0]
    assert last_find["fix_versions"] == "release-05-26-2026"


def test_filter_prs_has_jira_link_true(fake_collections):
    tools.filter.invoke({"source": "github", "filters": {"has_jira_link": True}, "limit": 5})
    last_find = fake_collections["github_prs"].find_calls[-1][0]
    assert last_find["jira_keys"] == {"$ne": []}


def test_filter_prs_has_jira_link_false(fake_collections):
    tools.filter.invoke({"source": "github", "filters": {"has_jira_link": False}, "limit": 5})
    last_find = fake_collections["github_prs"].find_calls[-1][0]
    assert last_find["jira_keys"] == {"$in": [[], None]}


def test_filter_unknown_source_reports_valid_sources(fake_collections):
    out = tools.filter.invoke({"source": "teams", "filters": {}})
    assert "error" in out
    assert "jira" in out["valid_sources"]


def test_filter_unknown_field_reports_valid_fields(fake_collections):
    out = tools.filter.invoke({"source": "github", "filters": {"nope": "x"}})
    assert "error" in out
    assert "author" in out["valid_fields"]
