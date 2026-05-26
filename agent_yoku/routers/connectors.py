"""Per-tenant connector management API.

Endpoints (all under /api/connectors, tenant-scoped via the auth dependency):

- GET    /                 list available + configured connectors (no secrets)
- PUT    /jira             upsert JIRA config (admin)
- PUT    /github           upsert GitHub config (admin)
- DELETE /{name}           remove a config (admin)
- POST   /{name}/sync      kick off ingest in a background task (admin)

The sync task captures the current tenant id + decrypted config from the
request thread and rebinds them inside the background coroutine so the ingest
runs against the right db with the right creds.
"""

from __future__ import annotations

import traceback

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from agent_yoku.connectors._runtime import (
    GithubConfig,
    JiraConfig,
    github_config_from_dict,
    jira_config_from_dict,
    use_github,
    use_jira,
)
from agent_yoku.deps import AdminUser
from agent_yoku.log import get_logger
from agent_yoku.schemas import (
    ConnectorStatus,
    ConnectorSyncResponse,
    GithubConfigIn,
    JiraConfigIn,
)
from agent_yoku.storage import connector_configs as cc
from agent_yoku.storage import tenancy

router = APIRouter(prefix="/connectors", tags=["connectors"])

log = get_logger("connectors")


# ---------- Listing ----------


def _status_doc(name: str, doc: dict | None) -> ConnectorStatus:
    if not doc:
        return ConnectorStatus(name=name, configured=False)
    return ConnectorStatus(
        name=name,
        configured=True,
        config=doc.get("config") or {},
        last_synced_at=doc.get("last_synced_at"),
        last_sync_status=doc.get("last_sync_status"),
        last_sync_error=doc.get("last_sync_error"),
        updated_at=doc.get("updated_at"),
    )


@router.get("", response_model=list[ConnectorStatus])
async def list_connectors(_: AdminUser) -> list[ConnectorStatus]:
    """List every supported connector with its current status."""
    configured = {d["name"]: d for d in cc.list_configs()}
    return [_status_doc(name, configured.get(name)) for name in cc.SUPPORTED_CONNECTORS]


# ---------- Upsert ----------


def _resolve_token(name: str, incoming: str | None) -> str:
    """Either use the new token, or fall back to the previously stored one.

    Raises 400 if neither is available (first-time setup with no token).
    """
    if incoming:
        return incoming
    existing = cc.get_config_decrypted(name) or {}
    token = existing.get("token")
    if not token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{name}: token is required for first-time setup",
        )
    return token


@router.put("/jira", response_model=ConnectorStatus)
async def upsert_jira(payload: JiraConfigIn, admin: AdminUser) -> ConnectorStatus:
    """Store JIRA creds for the current tenant. Token is encrypted at rest."""
    token = _resolve_token("jira", payload.token)
    cc.upsert_config(
        name="jira",
        config={
            "base_url": payload.base_url.rstrip("/"),
            "email": payload.email,
            "project": payload.project,
        },
        secrets={"token": token},
        user_id=admin.id,
    )
    return _status_doc("jira", cc.get_config("jira"))


@router.put("/github", response_model=ConnectorStatus)
async def upsert_github(payload: GithubConfigIn, admin: AdminUser) -> ConnectorStatus:
    """Store GitHub creds for the current tenant. Token is encrypted at rest."""
    token = _resolve_token("github", payload.token)
    cc.upsert_config(
        name="github",
        config={
            "api_base": payload.api_base.rstrip("/"),
            "org": payload.org,
            "pr_lookback_days": payload.pr_lookback_days,
        },
        secrets={"token": token},
        user_id=admin.id,
    )
    return _status_doc("github", cc.get_config("github"))


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_connector(name: str, _: AdminUser) -> None:
    if name not in cc.SUPPORTED_CONNECTORS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown connector {name!r}")
    cc.delete_config(name)


# ---------- Sync ----------


def _run_jira_sync(tenant_id: str, cfg: JiraConfig) -> None:
    """Synchronous ingest for one tenant. Runs in the background task pool."""
    tenancy.set_tenant(tenant_id)
    try:
        with use_jira(cfg):
            from agent_yoku.connectors.jira import ingest as ingest_mod
            from agent_yoku.connectors.jira import users_ingest as users_mod

            ingest_mod.main()
            users_mod.main()
        cc.mark_synced("jira", ok=True)
    except Exception as e:
        log.exception("jira sync failed for tenant=%s", tenant_id)
        cc.mark_synced(
            "jira", ok=False, error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        )


def _run_github_sync(tenant_id: str, cfg: GithubConfig) -> None:
    tenancy.set_tenant(tenant_id)
    try:
        with use_github(cfg):
            from agent_yoku.connectors.github import ingest as ingest_mod
            from agent_yoku.connectors.github import users_ingest as users_mod

            ingest_mod.main()
            users_mod.main()
        cc.mark_synced("github", ok=True)
    except Exception as e:
        log.exception("github sync failed for tenant=%s", tenant_id)
        cc.mark_synced(
            "github", ok=False, error=f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        )


_SYNC_FNS = {
    "jira": (_run_jira_sync, jira_config_from_dict),
    "github": (_run_github_sync, github_config_from_dict),
}


@router.post("/{name}/sync", response_model=ConnectorSyncResponse)
async def sync_connector(
    name: str, admin: AdminUser, background: BackgroundTasks
) -> ConnectorSyncResponse:
    """Trigger an ingest for the named connector in the background.

    Returns immediately with `status=started`. Progress + outcome are written
    onto the config doc (`last_synced_at`, `last_sync_status`, `last_sync_error`)
    so the UI can poll the list endpoint to see when it finishes.
    """
    if name not in _SYNC_FNS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown connector {name!r}")

    decrypted = cc.get_config_decrypted(name)
    if not decrypted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{name} is not configured")

    runner, parse = _SYNC_FNS[name]
    try:
        cfg = parse(decrypted)
    except KeyError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"stored {name} config missing field {e.args[0]!r}; reconfigure it",
        ) from e

    background.add_task(runner, admin.tenant_id, cfg)
    return ConnectorSyncResponse(name=name, status="started")
