"""Central exception handlers — log-level-by-status, structured error body."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from yoku.core.logging import get_logger
from yoku.core.middleware.request_context import (
    correlation_id_context,
    request_id_context,
    tenant_id_context,
)

log = get_logger("api.errors")


def _ctx() -> dict[str, str]:
    return {
        "request_id": request_id_context.get() or "",
        "correlation_id": correlation_id_context.get() or "",
        "tenant_id": tenant_id_context.get() or "",
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        if status >= 500:
            log.error("HTTP %s %s → %s: %s", request.method, request.url.path, status, exc.detail)
        elif status >= 400:
            log.warning("HTTP %s %s → %s", request.method, request.url.path, status)
        detail = exc.detail if isinstance(exc.detail, dict | list) else {"message": exc.detail}
        return JSONResponse(
            status_code=status,
            content={"error": detail, "context": _ctx()},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        log.warning("422 validation error %s %s", request.method, request.url.path)
        # `exc.errors()` can carry non-JSON-serializable objects in each error's
        # `ctx` (e.g. the original ValueError raised by a field validator). Run it
        # through jsonable_encoder so any field-validator error yields a clean 422
        # instead of crashing the handler into a 500.
        detail = jsonable_encoder(exc.errors())
        return JSONResponse(
            status_code=422,
            content={
                "error": {"message": "Request validation failed", "detail": detail},
                "context": _ctx(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled exception %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"message": "Internal server error"},
                "context": _ctx(),
            },
        )
