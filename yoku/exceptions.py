"""Typed exceptions for yoku.

A single QueryAsatoError base with narrow subclasses the agent can catch
and surface as clean failure signals to the LLM.
"""

from __future__ import annotations


class QueryAsatoError(Exception):
    """Base class for all yoku domain errors."""


class NotFoundError(QueryAsatoError):
    """A requested entity did not exist."""


class TicketNotFound(NotFoundError):
    """JIRA ticket key did not resolve."""


class PRNotFound(NotFoundError):
    """GitHub PR key did not resolve."""


class UserNotFound(NotFoundError):
    """User identifier did not resolve to a unified_users record."""


class UpstreamError(QueryAsatoError):
    """A 4xx/5xx from an external API after retries."""


class RateLimited(UpstreamError):
    """Source API returned 429 or equivalent."""


class ConfigError(QueryAsatoError):
    """Required env / setting missing or invalid."""


class IndexEmpty(QueryAsatoError):
    """Embedding index has no documents — run ingest + embed first."""
