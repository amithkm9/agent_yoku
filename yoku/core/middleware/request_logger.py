"""Request logging middleware — method, path, status, duration."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from yoku.core.logging import get_logger

log = get_logger("api.requests")

_HEALTH_PATHS = frozenset({"/healthz", "/health"})


class RequestLogger(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        msg = "%s %s → %s (%.1fms)"
        args = (request.method, request.url.path, response.status_code, duration_ms)
        if request.url.path in _HEALTH_PATHS:
            log.debug(msg, *args)
        else:
            log.info(msg, *args)
        return response
