"""Shared logger.

Borrows the ContextVar pattern from asato-api: per-request request_id /
correlation_id / tenant_id are pulled from the RequestContext middleware
and stamped onto every record. Adds session_id (set in app.py / chat.py
around each conversation turn) for agent-yoku's chat correlation, plus
service_name + env for ops filtering.

Outputs to a rotating file plus stderr; flip LOG_JSON=1 to emit JSON-line
records for ingestion by Sentry/Loki/etc.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import ClassVar

from agent_yoku.middleware.request_context import (
    correlation_id_context,
    request_id_context,
    tenant_id_context,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _REPO_ROOT / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_PATH = _LOG_DIR / "agent_yoku.log"

SERVICE_NAME = "agent_yoku"
ENVIRONMENT = os.environ.get("ENV", "local")

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
if _log_level_name not in _LOG_LEVELS:
    print(
        f"Warning: Invalid LOG_LEVEL '{_log_level_name}'. Using 'INFO'. "
        f"Valid levels: {', '.join(_LOG_LEVELS)}"
    )
    _log_level_name = "INFO"
_LEVEL = _LOG_LEVELS[_log_level_name]

_JSON = os.environ.get("LOG_JSON", "").lower() in {"1", "true", "yes"}

_TEXT_FMT = (
    "%(asctime)s | %(levelname)s | Service: %(service_name)s | Env: %(env)s "
    "| %(filename)s:%(lineno)s | [tenant_id: %(tenant_id)s] "
    "| request_id: %(request_id)s | correlation_id: %(correlation_id)s "
    "| [sid=%(session_id)s] | %(message)s"
)

session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


def safe_log_str(value: object) -> str:
    """Escape line terminators so interpolated values can't forge log lines."""
    text = str(value) if value is not None else ""
    return text.replace("\n", "\\n").replace("\r", "\\r")


class _ContextFilter(logging.Filter):
    """Stamp service_name/env + request/correlation/tenant/session IDs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.service_name = SERVICE_NAME
        record.env = ENVIRONMENT
        record.request_id = request_id_context.get() or "unknown-request-id"
        record.correlation_id = correlation_id_context.get() or "unknown-correlation-id"
        record.tenant_id = tenant_id_context.get() or "unknown-tenant-id"
        record.session_id = (session_id_var.get() or "-")[:8]
        return True


class _AgentYokuFormatter(logging.Formatter):
    """Backfill context fields so records from libraries that use the root
    logger (e.g. `logging.getLogger(__name__)` in deps) still format cleanly.
    """

    _DEFAULTS: ClassVar[dict[str, str]] = {
        "service_name": "unknown-service",
        "env": "unknown-env",
        "tenant_id": "unknown-tenant-id",
        "request_id": "unknown-request-id",
        "correlation_id": "unknown-correlation-id",
        "session_id": "-",
    }

    def format(self, record: logging.LogRecord) -> str:
        for attr, default in self._DEFAULTS.items():
            if not hasattr(record, attr):
                setattr(record, attr, default)
        return super().format(record)


class _JsonFormatter(_AgentYokuFormatter):
    """JSON line formatter — one record per line."""

    def format(self, record: logging.LogRecord) -> str:
        for attr, default in self._DEFAULTS.items():
            if not hasattr(record, attr):
                setattr(record, attr, default)
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service_name": record.service_name,
            "env": record.env,
            "tenant_id": record.tenant_id,
            "request_id": record.request_id,
            "correlation_id": record.correlation_id,
            "session_id": record.session_id,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("agent_yoku")
    root.handlers = []
    root.setLevel(_LEVEL)
    root.propagate = False
    root.addFilter(_ContextFilter())

    text_formatter = _AgentYokuFormatter(_TEXT_FMT)
    json_formatter = _JsonFormatter()

    fh = RotatingFileHandler(_LOG_PATH, maxBytes=2_000_000, backupCount=3)
    fh.setLevel(_LEVEL)
    fh.addFilter(_ContextFilter())
    fh.setFormatter(json_formatter if _JSON else text_formatter)

    sh = logging.StreamHandler()
    sh.setLevel(_LEVEL)
    sh.addFilter(_ContextFilter())
    sh.setFormatter(text_formatter)  # human-readable on stderr regardless

    root.addHandler(fh)
    root.addHandler(sh)

    logging.getLogger("httpx").setLevel(logging.WARNING)

    _maybe_attach_sentry(root)
    _configured = True


def _maybe_attach_sentry(root: logging.Logger) -> None:
    """If SENTRY_DSN is set and sentry_sdk is installed, wire up error reporting.

    No-op otherwise — keeps Sentry an optional dependency.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        root.warning("SENTRY_DSN set but sentry_sdk not installed; skipping.")
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENV", ENVIRONMENT),
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=0.0,
    )
    root.info("sentry initialised")


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(f"agent_yoku.{name}")


def set_session(session_id: str | None) -> None:
    """Bind a session_id to the current async/sync context for log correlation."""
    session_id_var.set(session_id)
