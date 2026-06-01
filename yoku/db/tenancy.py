"""Tenant context — one mongo cluster, one db per tenant.

Pattern: a ContextVar holds the current tenant_id. Storage accessors read
it and route to `<mongo_db>_<tenant_id>`.

Set the tenant in three places:
- FastAPI: the `current_user` dependency calls `set_tenant(user.tenant_id)`.
- CLI: explicit `--tenant` flag on the root command.
- Streamlit: pulled from query param or session_state (legacy / dev mode).
"""

from __future__ import annotations

import re
from contextvars import ContextVar

from yoku.config import settings

_current: ContextVar[str | None] = ContextVar("tenant_id", default=None)

# Lowercase alphanumeric + `-` / `_`, 2-40 chars, must start with alphanumeric.
# Keeps tenant ids URL-safe and rules out trailing-whitespace / unicode-
# lookalike weirdness at the boundary.
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")


def is_valid_tenant_id(tenant_id: str) -> bool:
    return bool(_TENANT_RE.match(tenant_id))


def normalize_tenant_id(tenant_id: str) -> str:
    """Lowercase + strip. Caller should validate the result with `is_valid_tenant_id`."""
    return tenant_id.strip().lower()


def set_tenant(tenant_id: str | None) -> None:
    _current.set(tenant_id)


def current_tenant() -> str:
    """Return the active tenant, raising if none has been bound."""
    tenant_id = _current.get()
    if not tenant_id:
        raise RuntimeError("tenant context is not set")
    return tenant_id


def tenant_db_name(tenant_id: str | None = None) -> str:
    """Map a tenant_id to its mongo database name.

    Every tenant gets its own suffixed db name:
    `<mongo_db>_<tenant_id>` (alphanumerics + `-_` preserved, anything else
    replaced with `_`).
    """
    t = tenant_id or current_tenant()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in t)
    return f"{settings.mongo_db}_{safe}"
