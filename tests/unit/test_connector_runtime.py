"""Unit tests for the connector runtime ContextVar plumbing.

Verifies that:
- `current_jira_config()` returns the env-driven default outside `use_jira`.
- `use_jira(cfg)` rebinds the config within the `with` block and restores on exit.
- Same for GitHub.
"""

from __future__ import annotations

from agent_yoku.connectors._runtime import (
    GithubConfig,
    JiraConfig,
    current_github_config,
    current_jira_config,
    github_config_from_dict,
    jira_config_from_dict,
    use_github,
    use_jira,
)


def test_jira_env_default():
    cfg = current_jira_config()
    # Stub env vars from conftest produce these defaults.
    assert cfg.email == "test@example.com"
    assert cfg.token == "test-token"
    assert cfg.base_url_clean.endswith(".atlassian.net")


def test_jira_use_rebinds_within_block():
    custom = JiraConfig(
        base_url="https://acme.atlassian.net",
        email="alice@acme.com",
        token="acme-token",
        project="ACME",
    )
    before = current_jira_config()
    with use_jira(custom):
        assert current_jira_config() is custom
    after = current_jira_config()
    # ContextVar reset restored the previous binding (env default).
    assert after.token == before.token


def test_github_use_rebinds_within_block():
    custom = GithubConfig(
        api_base="https://api.github.com",
        token="gh-token",
        org="acme",
        pr_lookback_days=30,
    )
    with use_github(custom):
        assert current_github_config() is custom
    # Outside the block the env default is back.
    assert current_github_config().token == "test-token"


def test_jira_config_from_dict_normalises():
    cfg = jira_config_from_dict(
        {
            "base_url": "https://acme.atlassian.net/",  # trailing slash
            "email": "x@y.com",
            "token": "tok",
            # project omitted -> fall back to settings.jira_project
        }
    )
    assert cfg.base_url == "https://acme.atlassian.net"
    assert cfg.project  # non-empty from settings


def test_github_config_from_dict_defaults():
    cfg = github_config_from_dict({"token": "tok", "org": "acme"})
    assert cfg.api_base.startswith("https://api.github.com")
    assert cfg.pr_lookback_days > 0
