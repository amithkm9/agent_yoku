"""Tenant context — one mongo cluster, one db per tenant.

Pattern: a ContextVar holds the current tenant_id. Storage accessors read
it and route to `<mongo_db>_<tenant_id>` (with the special-case that the
default tenant maps to the bare `<mongo_db>` so single-tenant deploys don't
collide with a redundant suffix).

Set the tenant in three places:
- FastAPI: the `current_user` dependency calls `set_tenant(user.tenant_id)`.
- CLI: explicit `--tenant` flag (defaults to settings.default_tenant_id).
- Streamlit: pulled from query param or session_state (legacy / dev mode).
"""

from __future__ import annotations

from contextvars import ContextVar

from agent_yoku.config import settings

_current: ContextVar[str | None] = ContextVar("tenant_id", default=None)


def set_tenant(tenant_id: str | None) -> None:
    _current.set(tenant_id)


def current_tenant() -> str:
    """Return the active tenant, falling back to settings.default_tenant_id."""
    return _current.get() or settings.default_tenant_id


def tenant_db_name(tenant_id: str | None = None) -> str:
    """Map a tenant_id to its mongo database name.

    The configured default tenant maps to the bare `<mongo_db>` so a fresh
    install or single-tenant deploy doesn't end up with `<mongo_db>_default`.
    All other tenants get `<mongo_db>_<tenant_id>` (alphanumerics + `-_`
    preserved, anything else replaced with `_`).
    """
    t = tenant_id or current_tenant()
    if t == settings.default_tenant_id:
        return settings.mongo_db
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in t)
    return f"{settings.mongo_db}_{safe}"
