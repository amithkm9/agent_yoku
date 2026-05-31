"""FastAPI app — surfaces auth, sessions, chat, stats to the React UI."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from yoku.config import settings
from yoku.log import get_logger
from yoku.middleware import RateLimit, RequestContext
from yoku.middleware.exception_handler import register_exception_handlers
from yoku.middleware.request_logger import RequestLogger
from yoku.routers import auth, chat, connectors, sessions, stats

log = get_logger("api.main")

# Environments where the built-in dev JWT secret is tolerated.
_SAFE_ENVS = {"local", "dev", "development", "test", "ci"}


def create_app() -> FastAPI:
    env = os.environ.get("ENV", "local")
    if settings.is_default_jwt_secret and env.lower() not in _SAFE_ENVS:
        raise RuntimeError(
            f"JWT_SECRET is the built-in dev default but ENV={env!r}; set a strong "
            "JWT_SECRET before running outside local/dev."
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Background auto-sync runs the per-tenant refresh pipeline on an
        # interval. Kept off in test/ci so the suite never spawns a live
        # scheduler thread or touches the network.
        from yoku import scheduler

        run_auto_sync = settings.auto_sync_enabled and env.lower() not in {"test", "ci"}
        if run_auto_sync:
            scheduler.start()
        else:
            log.info("auto-sync disabled (enabled=%s, env=%s)", settings.auto_sync_enabled, env)
        try:
            yield
        finally:
            scheduler.shutdown()  # safe no-op if never started

    app = FastAPI(
        title="yoku",
        version="0.1.0",
        description="JIRA + GitHub deepagent — REST surface.",
        # FastAPI mounts /docs and /redoc by default; that's fine here.
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.add_middleware(RequestContext)
    app.add_middleware(RequestLogger)

    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimit,
            limit=settings.rate_limit_requests,
            window_s=settings.rate_limit_window_s,
            exempt_paths=("/healthz", "/docs", "/openapi.json", "/redoc"),
            trust_forwarded_for=settings.rate_limit_trust_forwarded_for,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(connectors.router, prefix="/api")

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        from yoku.storage.mongo import ping

        try:
            ping()
        except Exception:
            log.warning("healthz: mongo unreachable")
            return JSONResponse(  # type: ignore[return-value]
                {"status": "degraded", "mongo": "unreachable"}, status_code=503
            )
        return {"status": "ok"}

    log.info("FastAPI app initialised — routers + middleware wired")
    return app


app = create_app()
