"""Shared logger.

Borrows the ContextVar pattern from asato-svc but scoped to agent_yoku's
needs (single user, per-session correlation, no tenancy). Outputs to a
rotating file plus stderr; flip LOG_JSON=1 to emit JSON-line records for
ingestion by Sentry/Loki/etc.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _REPO_ROOT / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_PATH = _LOG_DIR / "agent_yoku.log"

_LEVEL = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
_JSON = os.environ.get("LOG_JSON", "").lower() in {"1", "true", "yes"}

_TEXT_FMT = "%(asctime)s %(levelname)-5s %(name)s [sid=%(session_id)s] | %(message)s"

# Per-conversation correlation. Set in app.py / chat.py around each turn.
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


class _SessionFilter(logging.Filter):
    """Inject the current session_id into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = (session_id_var.get() or "-")[:8]
        return True


class _JsonFormatter(logging.Formatter):
    """Minimal JSON line formatter — one record per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "session_id": getattr(record, "session_id", "-"),
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
    root.setLevel(_LEVEL)
    root.propagate = False
    root.addFilter(_SessionFilter())

    text_formatter = logging.Formatter(_TEXT_FMT)
    json_formatter = _JsonFormatter()

    fh = RotatingFileHandler(_LOG_PATH, maxBytes=2_000_000, backupCount=3)
    fh.addFilter(_SessionFilter())
    fh.setFormatter(json_formatter if _JSON else text_formatter)

    sh = logging.StreamHandler()
    sh.addFilter(_SessionFilter())
    sh.setFormatter(text_formatter)  # human-readable on stderr regardless

    root.addHandler(fh)
    root.addHandler(sh)

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
        environment=os.environ.get("SENTRY_ENV", "local"),
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
