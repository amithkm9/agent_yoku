"""Route metadata regressions that should hold across FastAPI versions."""

from __future__ import annotations

from fastapi.routing import APIRoute


def _route(app, path: str, method: str) -> APIRoute:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route not found: {method} {path}")


def test_204_delete_routes_disable_response_models():
    from yoku.api.main import app

    connector_delete = _route(app, "/api/connectors/{name}", "DELETE")
    session_delete = _route(app, "/api/sessions/{session_id}", "DELETE")

    assert connector_delete.status_code == 204
    assert connector_delete.response_model is None
    assert session_delete.status_code == 204
    assert session_delete.response_model is None
