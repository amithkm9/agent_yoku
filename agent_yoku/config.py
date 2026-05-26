"""Typed configuration via Pydantic Settings.

All env-driven config lives here. Downstream modules import `settings` and
use typed attributes; secrets are wrapped in SecretStr so they don't leak
into logs accidentally.

`load_dotenv` also runs so SDKs that read os.environ directly (openai,
langchain) find their keys.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Hydrate os.environ for SDKs that read directly.
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path.home() / "Desktop" / ".env", override=False)


class Settings(BaseSettings):
    """All env-driven config in one place."""

    model_config = SettingsConfigDict(
        env_file=[str(_REPO_ROOT / ".env"), str(Path.home() / "Desktop" / ".env")],
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # JIRA — defaults point at the bundled-data source; override in .env.
    jira_email: str
    jira_token: SecretStr
    jira_base_url: str = "https://asato-ai.atlassian.net"
    jira_project: str = "AS"

    # GitHub — `github_org` names the org whose PRs get ingested; override per deploy.
    github_token: SecretStr
    github_org: str = "AsatoCorp"
    github_api_base: str = "https://api.github.com"
    github_pr_lookback_days: int = 365

    # OpenAI
    openai_api_key: SecretStr
    openai_embed_model: str = Field(default="text-embedding-3-small")
    openai_chat_model: str = Field(default="gpt-5.4-mini")
    chat_model_provider: str = "openai"

    # Mongo
    mongo_uri: str = "mongodb://localhost:27017/"
    mongo_db: str = "agent_yoku"

    # Observability
    sentry_dsn: SecretStr | None = None
    log_level: str = "INFO"
    log_json: bool = False

    # Auth (FastAPI / JWT)
    jwt_secret: SecretStr = Field(
        default=SecretStr("change-me-in-prod-please-32-chars-min"),
        description="HS256 signing secret. MUST be overridden in production.",
    )
    jwt_ttl_hours: int = 24

    # Optional dedicated key for encrypting per-tenant connector secrets at rest.
    # Falls back to `jwt_secret` if unset — fine for dev. Override in prod so
    # rotating JWT secrets doesn't invalidate stored connector tokens.
    connector_secret_key: SecretStr | None = None

    # Derived
    @property
    def agent_model_id(self) -> str:
        return f"{self.chat_model_provider}:{self.openai_chat_model}"

    @property
    def jira_base_url_clean(self) -> str:
        return self.jira_base_url.rstrip("/")

    @property
    def github_api_base_clean(self) -> str:
        return self.github_api_base.rstrip("/")


settings = Settings()


def openai_client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key.get_secret_value())


# ---------- Back-compat re-exports ----------
# Existing modules still import these constants by name. Wrap once here so the
# rest of the codebase doesn't need to touch SecretStr semantics.

JIRA_EMAIL = settings.jira_email
JIRA_TOKEN = settings.jira_token.get_secret_value()
JIRA_BASE_URL = settings.jira_base_url_clean
JIRA_PROJECT = settings.jira_project

GITHUB_TOKEN = settings.github_token.get_secret_value()
GITHUB_ORG = settings.github_org
GITHUB_API_BASE = settings.github_api_base_clean
GITHUB_PR_LOOKBACK_DAYS = settings.github_pr_lookback_days

OPENAI_API_KEY = settings.openai_api_key.get_secret_value()
EMBED_MODEL = settings.openai_embed_model
CHAT_MODEL = settings.openai_chat_model
CHAT_MODEL_PROVIDER = settings.chat_model_provider
AGENT_MODEL_ID = settings.agent_model_id

MONGO_URI = settings.mongo_uri
MONGO_DB = settings.mongo_db

# Storage re-exports — lazy via PEP 562 to avoid `config <-> storage.mongo`
# circular import. Legacy callers can still do
# `from agent_yoku.config import tickets_collection`. New code should prefer
# `from agent_yoku.storage.mongo import ...`.
_LAZY_STORAGE_EXPORTS = {
    "ALLOWED_COLLECTIONS",
    "get_collection",
    "tickets_collection",
    "users_collection",
    "github_prs_collection",
    "github_users_collection",
    "unified_users_collection",
    "chat_sessions_collection",
    "chat_messages_collection",
}


def __getattr__(name: str):
    if name in _LAZY_STORAGE_EXPORTS:
        from agent_yoku.storage import mongo as _mongo

        value = getattr(_mongo, name)
        globals()[name] = value  # cache for subsequent accesses
        return value
    raise AttributeError(f"module 'agent_yoku.config' has no attribute {name!r}")


# `__all__` lists only eagerly-defined names. Lazy storage exports
# (ALLOWED_COLLECTIONS, *_collection, get_collection) are still importable via
# __getattr__ but excluded here so ruff doesn't flag them as undefined.
__all__ = [
    "AGENT_MODEL_ID",
    "CHAT_MODEL",
    "CHAT_MODEL_PROVIDER",
    "EMBED_MODEL",
    "GITHUB_API_BASE",
    "GITHUB_ORG",
    "GITHUB_PR_LOOKBACK_DAYS",
    "GITHUB_TOKEN",
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_PROJECT",
    "JIRA_TOKEN",
    "MONGO_DB",
    "MONGO_URI",
    "OPENAI_API_KEY",
    "Settings",
    "openai_client",
    "settings",
]
