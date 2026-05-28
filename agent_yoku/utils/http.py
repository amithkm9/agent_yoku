"""Shared HTTP retry policy for connector clients.

`is_retryable` + `make_retry()` are reused by JIRA and GitHub clients (and any
future connector that talks REST). One place to tune retry semantics.
"""

from __future__ import annotations

import logging

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

TRANSIENT = (requests.ConnectionError, requests.Timeout)

RETRY_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
FATAL_CODES: frozenset[int] = frozenset({401, 403, 404})


def is_retryable(exc: BaseException, source: str, log: logging.Logger) -> bool:
    """True iff a transient connection error or a retry-worthy HTTP code."""
    if isinstance(exc, TRANSIENT):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        if code in RETRY_CODES:
            log.warning("transient %s error status=%s", source, code)
            return True
    return False


def make_retry(source: str, log: logging.Logger, attempts: int = 4):
    """Return a tenacity `@retry` decorator pre-wired for HTTP calls.

    Args:
        source: human label that appears in retry warnings ("jira", "github").
        log: logger to emit retry warnings on.
        attempts: max attempts (default 4 — covers 3 retries after the first
            try with exponential backoff up to ~10s).
    """
    return retry(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=1, max=10),
        retry=retry_if_exception(lambda exc: is_retryable(exc, source, log)),
        reraise=True,
    )
