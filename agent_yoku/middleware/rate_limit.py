"""In-process fixed-window rate limiting.

A per-client (IP) request cap to blunt brute-force and abuse. It's in-memory and
therefore per-process: behind multiple workers each enforces its own share, so
it's a first line of defense, not a hard global limit — put a shared store
(redis) in front if you need that. Configurable via settings; exempt paths skip.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_PRUNE_AT = 10_000  # prune expired client entries once the table grows past this


class RateLimit(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        limit: int,
        window_s: int,
        exempt_paths: Iterable[str] = (),
        trust_forwarded_for: bool = False,
    ) -> None:
        super().__init__(app)
        self.limit = max(1, limit)
        self.window_s = max(1, window_s)
        self.exempt = tuple(exempt_paths)
        # Only honor X-Forwarded-For behind a trusted proxy; otherwise any client
        # could spoof it and rotate IPs to bypass the limit. Default: socket IP.
        self.trust_forwarded_for = trust_forwarded_for
        self._hits: dict[str, tuple[float, int]] = {}  # client -> (window_start, count)
        self._lock = threading.Lock()

    def _client(self, request: Request) -> str:
        if self.trust_forwarded_for:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _check(self, key: str) -> tuple[bool, int]:
        """Count this hit; return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > _PRUNE_AT:
                self._hits = {
                    k: (s, c) for k, (s, c) in self._hits.items() if now - s < self.window_s
                }
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self.window_s:
                start, count = now, 0  # window rolled over
            count += 1
            self._hits[key] = (start, count)
            if count > self.limit:
                return False, int(self.window_s - (now - start)) + 1
            return True, 0

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or any(path.startswith(p) for p in self.exempt):
            return await call_next(request)
        allowed, retry_after = self._check(self._client(request))
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
