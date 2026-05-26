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

from agent_yoku.config import settings


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


_jira: ContextVar[JiraConfig | None] = ContextVar("jira_config", default=None)
_github: ContextVar[GithubConfig | None] = ContextVar("github_config", default=None)


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
