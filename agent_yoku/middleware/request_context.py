"""Per-request correlation IDs surfaced to the logger.

Mirrors asato-api's RequestContext middleware: pulls X-Request-ID /
X-Correlation-ID / X-Tenant-ID off the incoming request (generating uuids
when absent) and binds them to ContextVars so log records emitted during
the request carry the same IDs. The response echoes the correlation/
tenant IDs back so callers can stitch traces across services.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

request_id_context: ContextVar[str] = ContextVar("request_id", default="")
correlation_id_context: ContextVar[str] = ContextVar("correlation_id", default="")
tenant_id_context: ContextVar[str] = ContextVar("tenant_id", default="")


class RequestContext(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        tenant_id = request.headers.get("X-Tenant-ID", "unknown-tenant")

        request_id_context.set(request_id)
        correlation_id_context.set(correlation_id)
        tenant_id_context.set(tenant_id)

        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Tenant-ID"] = tenant_id
        return response
