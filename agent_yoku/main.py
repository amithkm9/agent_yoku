"""FastAPI app — surfaces auth, sessions, chat, stats to the React UI."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_yoku.log import get_logger
from agent_yoku.middleware import RequestContext
from agent_yoku.routers import auth, chat, connectors, sessions, stats

log = get_logger("api.main")


def create_app() -> FastAPI:
    app = FastAPI(
        title="agent_yoku",
        version="0.1.0",
        description="JIRA + GitHub deepagent — REST surface.",
        # FastAPI mounts /docs and /redoc by default; that's fine here.
    )

    # Request-correlation middleware must run before CORS so log records
    # emitted during preflight responses still carry the IDs.
    app.add_middleware(RequestContext)

    # Vite dev server runs on 5173; allow it + same-origin.
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
    async def healthz() -> dict:
        return {"status": "ok"}

    log.info("FastAPI app initialised — routers + middleware wired")
    return app


app = create_app()
