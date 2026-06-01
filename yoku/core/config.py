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

# The shipped dev JWT secret. Booting prod with this still set is refused in
# create_app(); kept as a named constant so the check can't drift from the default.
DEFAULT_JWT_SECRET = "change-me-in-prod-please-32-chars-min"

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

    jira_email: str
    jira_token: SecretStr
    jira_base_url: str = "https://asato-ai.atlassian.net"
    jira_project: str = "AS"

    github_token: SecretStr
    github_org: str = "AsatoCorp"
    github_api_base: str = "https://api.github.com"
    github_pr_lookback_days: int = 365

    slack_bot_token: SecretStr | None = None
    slack_workspace: str | None = None
    slack_lookback_days: int = 90
    slack_channel_types: str = "public_channel"

    openai_api_key: SecretStr
    openai_embed_model: str = Field(default="text-embedding-3-small")
    openai_chat_model: str = Field(default="gpt-5.4-mini")
    chat_model_provider: str = "openai"

    # ZeroEntropy second-stage reranker over hybrid-search candidates. Active only
    # when both the flag is on and the key is set; otherwise search falls back to
    # the built-in feature reranker. rerank_top_n caps how many top candidates are
    # sent per call (bounds token cost + latency on the hot path).
    rerank_enabled: bool = True
    rerank_model: str = "zerank-2"
    rerank_top_n: int = 50
    rerank_timeout_s: float = 3.0
    zeroentropy_api_key: SecretStr | None = None

    @property
    def rerank_active(self) -> bool:
        """True when ZeroEntropy reranking is both enabled and configured."""
        return self.rerank_enabled and self.zeroentropy_api_key is not None

    mongo_uri: str = "mongodb://localhost:27017/"
    mongo_db: str = "yoku"

    sentry_dsn: SecretStr | None = None
    log_level: str = "INFO"
    log_json: bool = False

    jwt_secret: SecretStr = Field(
        default=SecretStr(DEFAULT_JWT_SECRET),
        description="HS256 signing secret. MUST be overridden in production.",
    )
    jwt_ttl_hours: int = 24

    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_s: int = 60
    # Only enable behind a trusted reverse proxy that sets X-Forwarded-For;
    # otherwise clients can spoof the header to dodge the limit.
    rate_limit_trust_forwarded_for: bool = False

    # How many recent turns of a session to replay into the agent (bounds token
    # cost on long chats; the full transcript is still kept for UI replay).
    chat_history_max_turns: int = 12

    # Background auto-sync — periodically refresh every tenant's connectors
    # (ingest -> embed -> link) without anyone clicking "sync". Driven by the
    # scheduler started in the API lifespan. Disable in prod by setting
    # AUTO_SYNC_ENABLED=false; tune cadence with SYNC_INTERVAL_MINUTES.
    auto_sync_enabled: bool = True
    sync_interval_minutes: int = 60

    @property
    def is_default_jwt_secret(self) -> bool:
        """True when the built-in dev secret is still in use (unsafe in prod)."""
        return self.jwt_secret.get_secret_value() == DEFAULT_JWT_SECRET

    # Optional dedicated key for encrypting per-tenant connector secrets at rest.
    # Falls back to `jwt_secret` if unset — fine for dev. Override in prod so
    # rotating JWT secrets doesn't invalidate stored connector tokens.
    connector_secret_key: SecretStr | None = None

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


# Legacy convenience aliases — prefer `settings.openai_embed_model` in new code.
EMBED_MODEL = settings.openai_embed_model
AGENT_MODEL_ID = settings.agent_model_id

# Storage re-exports — lazy via PEP 562 to avoid `config <-> storage.mongo`
# circular import. New code should prefer `from yoku.core.storage.mongo import ...`.
_LAZY_STORAGE_EXPORTS = {
    "ALLOWED_COLLECTIONS",
    "get_collection",
    # dc-* raw connector accessors
    "dc_jira_collection",
    "dc_jira_users_collection",
    "dc_github_collection",
    "dc_github_users_collection",
    "dc_slack_collection",
    "dc_slack_users_collection",
    # ds-* canonical accessors
    "ds_work_item_collection",
    "ds_pull_request_collection",
    "ds_conversation_collection",
    "ds_entity_links_collection",
    "ds_unified_users_collection",
    # session / auth (unchanged)
    "chat_sessions_collection",
    "chat_messages_collection",
}


def __getattr__(name: str):
    if name in _LAZY_STORAGE_EXPORTS:
        from yoku.core.storage import mongo as _mongo

        value = getattr(_mongo, name)
        globals()[name] = value  # cache for subsequent accesses
        return value
    raise AttributeError(f"module 'yoku.core.config' has no attribute {name!r}")


# `__all__` lists only eagerly-defined names. Lazy storage exports
# (ALLOWED_COLLECTIONS, *_collection, get_collection) are still importable via
# __getattr__ but excluded here so ruff doesn't flag them as undefined.
__all__ = [
    "AGENT_MODEL_ID",
    "EMBED_MODEL",
    "Settings",
    "openai_client",
    "settings",
]
