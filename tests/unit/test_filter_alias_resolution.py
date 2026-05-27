"""Verify filter_jira / filter_prs translate aliases via unified_users."""

from __future__ import annotations

from agent_yoku.agent import tools


def test_filter_jira_translates_github_login_to_jira_name(fake_collections):
    fake_collections["unified_users"].docs = [
        {"jira": {"displayName": "Akshay Reddy"}, "github": {"login": "internet-zero"}},
    ]
    tools.filter_jira.invoke({"assignee": "internet-zero", "limit": 5})

    last_find = fake_collections["jira_tickets"].find_calls[-1][0]
    assert last_find["assignee"] == "Akshay Reddy"


def test_filter_jira_passes_through_when_no_alias(fake_collections):
    fake_collections["unified_users"].docs = []
    tools.filter_jira.invoke({"assignee": "Some Random", "limit": 5})

    last_find = fake_collections["jira_tickets"].find_calls[-1][0]
    assert last_find["assignee"] == "Some Random"


def test_filter_prs_translates_jira_displayname_to_gh_login(fake_collections):
    fake_collections["unified_users"].docs = [
        {"jira": {"displayName": "Akshay Reddy"}, "github": {"login": "internet-zero"}},
    ]
    tools.filter_prs.invoke({"author": "Akshay Reddy", "limit": 5})

    last_find = fake_collections["github_prs"].find_calls[-1][0]
    assert last_find["author"] == "internet-zero"


def test_filter_jira_applies_fix_version(fake_collections):
    tools.filter_jira.invoke({"fix_version": "release-05-26-2026", "limit": 5})
    last_find = fake_collections["jira_tickets"].find_calls[-1][0]
    assert last_find["fix_versions"] == "release-05-26-2026"


def test_filter_prs_has_jira_link_true(fake_collections):
    tools.filter_prs.invoke({"has_jira_link": True, "limit": 5})
    last_find = fake_collections["github_prs"].find_calls[-1][0]
    assert last_find["jira_keys"] == {"$ne": []}


def test_filter_prs_has_jira_link_false(fake_collections):
    tools.filter_prs.invoke({"has_jira_link": False, "limit": 5})
    last_find = fake_collections["github_prs"].find_calls[-1][0]
    assert last_find["jira_keys"] == {"$in": [[], None]}
