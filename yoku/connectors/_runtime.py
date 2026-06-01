"""Per-call connector configuration plumbing.

Connector clients used to read module-level constants populated from env at
import time (`JIRA_TOKEN`, `GITHUB_TOKEN`, …). That made every connector
single-tenant: all tenants ingested from the same upstream.

This module replaces that with a tiny ContextVar + dataclass pair per
connector:

- `JiraConfig` / `GithubConfig` carry everything a client needs (creds, base
  url, project / org).
- `use_jira(cfg)` / `use_github(cfg)` are context managers that bind a config
  for the duration of a `with` block.
- `current_jira_config()` / `current_github_config()` are called inside client
  functions and return the bound config, falling back to the env-driven
  defaults from `settings` so existing CLI workflows still work without
  per-tenant connector configs in mongo.

The fallback path is deliberate: dev workflows still work the old way without
populating any per-tenant config in mongo.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from yoku.config import settings

_SLACK_LOOKBACK_DAYS_DEFAULT = 90


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    token: str
    project: str

    @property
    def base_url_clean(self) -> str:
        return self.base_url.rstrip("/")


@dataclass(frozen=True)
class GithubConfig:
    api_base: str
    token: str
    org: str
    pr_lookback_days: int

    @property
    def api_base_clean(self) -> str:
        return self.api_base.rstrip("/")


@dataclass(frozen=True)
class SlackConfig:
    bot_token: str
    workspace: str
    lookback_days: int = _SLACK_LOOKBACK_DAYS_DEFAULT
    channel_types: str = "public_channel"


_jira: ContextVar[JiraConfig | None] = ContextVar("jira_config", default=None)
_github: ContextVar[GithubConfig | None] = ContextVar("github_config", default=None)
_slack: ContextVar[SlackConfig | None] = ContextVar("slack_config", default=None)


def _env_jira_config() -> JiraConfig:
    return JiraConfig(
        base_url=settings.jira_base_url_clean,
        email=settings.jira_email,
        token=settings.jira_token.get_secret_value(),
        project=settings.jira_project,
    )


def _env_github_config() -> GithubConfig:
    return GithubConfig(
        api_base=settings.github_api_base_clean,
        token=settings.github_token.get_secret_value(),
        org=settings.github_org,
        pr_lookback_days=settings.github_pr_lookback_days,
    )


def current_jira_config() -> JiraConfig:
    """Return the bound JIRA config, or the env-driven default."""
    return _jira.get() or _env_jira_config()


def current_github_config() -> GithubConfig:
    """Return the bound GitHub config, or the env-driven default."""
    return _github.get() or _env_github_config()


def _env_slack_config() -> SlackConfig | None:
    token = settings.slack_bot_token
    workspace = settings.slack_workspace
    if not token or not workspace:
        return None
    return SlackConfig(
        bot_token=token.get_secret_value(),
        workspace=workspace,
        lookback_days=settings.slack_lookback_days,
        channel_types=settings.slack_channel_types,
    )


def current_slack_config() -> SlackConfig:
    cfg = _slack.get()
    if cfg is not None:
        return cfg
    env_cfg = _env_slack_config()
    if env_cfg is not None:
        return env_cfg
    raise RuntimeError(
        "no Slack config bound — call use_slack(cfg) first or set SLACK_BOT_TOKEN + SLACK_WORKSPACE"
    )


@contextmanager
def use_jira(cfg: JiraConfig) -> Iterator[None]:
    token = _jira.set(cfg)
    try:
        yield
    finally:
        _jira.reset(token)


@contextmanager
def use_github(cfg: GithubConfig) -> Iterator[None]:
    token = _github.set(cfg)
    try:
        yield
    finally:
        _github.reset(token)


@contextmanager
def use_slack(cfg: SlackConfig) -> Iterator[None]:
    token = _slack.set(cfg)
    try:
        yield
    finally:
        _slack.reset(token)


def jira_config_from_dict(d: dict) -> JiraConfig:
    return JiraConfig(
        base_url=(d.get("base_url") or "").rstrip("/"),
        email=d["email"],
        token=d["token"],
        project=d.get("project") or settings.jira_project,
    )


def github_config_from_dict(d: dict) -> GithubConfig:
    return GithubConfig(
        api_base=(d.get("api_base") or "https://api.github.com").rstrip("/"),
        token=d["token"],
        org=d["org"],
        pr_lookback_days=int(d.get("pr_lookback_days") or settings.github_pr_lookback_days),
    )


def slack_config_from_dict(d: dict) -> SlackConfig:
    return SlackConfig(
        bot_token=d["bot_token"],
        workspace=d["workspace"],
        lookback_days=int(d.get("lookback_days") or _SLACK_LOOKBACK_DAYS_DEFAULT),
        channel_types=d.get("channel_types") or "public_channel",
    )
