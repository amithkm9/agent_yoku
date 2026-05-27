"""Tenant sync orchestration shared by the connectors API and the background
scheduler.

`run_full_pipeline_for_tenant` runs the whole refresh pipeline for one tenant —
ingest every configured connector, then embed deltas, unify users, link PRs to
JIRA, and backfill author emails — mirroring the `refresh-all` CLI but using the
tenant's own stored connector credentials. `run_all_tenants` fans that out
across every tenant that has at least one connector configured; the scheduler
calls it on an interval.

Everything here is tenant-scoped via `tenancy.set_tenant`, blocking (network +
OpenAI), and best-effort: one connector or tenant failing is logged and recorded
on the config doc, never allowed to abort the rest of the run.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from agent_yoku.agent.tools import invalidate_index
from agent_yoku.config import settings
from agent_yoku.connectors._runtime import (
    github_config_from_dict,
    jira_config_from_dict,
    use_github,
    use_jira,
)
from agent_yoku.log import get_logger
from agent_yoku.storage import connector_configs as cc
from agent_yoku.storage import tenancy

log = get_logger("sync_service")


@contextmanager
def _clean_argv(prog: str) -> Iterator[None]:
    """Blank `sys.argv` for the duration of a legacy CLI-style `main()` call.

    The embed/link `main()` entry points sniff `sys.argv` for flags (`--all`,
    `--reset`). Invoked in-process (API / scheduler) the ambient argv belongs to
    uvicorn, so we replace it with just the program name to get default
    behaviour, then restore it.
    """
    saved = sys.argv
    sys.argv = [prog]
    try:
        yield
    finally:
        sys.argv = saved


def _fmt_error(e: Exception) -> str:
    """Compact error string stored on the connector config doc for the UI."""
    return f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"


def _ingest_jira(decrypted: dict) -> None:
    with use_jira(jira_config_from_dict(decrypted)):
        from agent_yoku.connectors.jira import ingest as ingest_mod
        from agent_yoku.connectors.jira import users_ingest as users_mod

        ingest_mod.main()
        users_mod.main()


def _ingest_github(decrypted: dict) -> None:
    with use_github(github_config_from_dict(decrypted)):
        from agent_yoku.connectors.github import ingest as ingest_mod
        from agent_yoku.connectors.github import users_ingest as users_mod

        ingest_mod.main()
        users_mod.main()


_INGEST_FNS: dict[str, Callable[[dict], None]] = {
    "jira": _ingest_jira,
    "github": _ingest_github,
}


def _ingest_connector(name: str) -> bool:
    """Ingest one connector for the currently-bound tenant.

    Returns True if the connector is configured (and an ingest was attempted),
    False if it isn't configured for this tenant. Records the outcome on the
    config doc and never raises — a failure in one connector must not abort the
    others.
    """
    tenant_id = tenancy.current_tenant()
    decrypted = cc.get_config_decrypted(name)
    if not decrypted:
        return False
    try:
        _INGEST_FNS[name](decrypted)
        cc.mark_synced(name, ok=True)
    except Exception as e:
        log.exception("%s ingest failed for tenant=%s", name, tenant_id)
        cc.mark_synced(name, ok=False, error=_fmt_error(e))
    return True


def _run_post_ingest_pipeline() -> None:
    """Embed deltas, unify users, link PRs to JIRA, backfill author emails.

    Operates on whichever tenant is currently bound. Same order as the
    `refresh-all` CLI.
    """
    from agent_yoku.embeddings import embed as embed_mod
    from agent_yoku.linking import pr_to_jira
    from agent_yoku.storage import backfill, unified_users

    with _clean_argv("embed"):
        embed_mod.main()
    unified_users.main()
    with _clean_argv("link"):
        pr_to_jira.main()
    backfill.main()


def list_tenant_ids() -> list[str]:
    """Every tenant that has its own mongo database.

    Tenant dbs are named ``<mongo_db>_<tenant_id>`` (see
    `tenancy.tenant_db_name`). Valid tenant ids contain only url-safe chars, so
    the suffix round-trips back to the tenant id unchanged. Mongo's own
    `admin`/`config`/`local` dbs and the unsuffixed base db are excluded.
    """
    from agent_yoku.storage.mongo import _client

    prefix = f"{settings.mongo_db}_"
    names = _client().list_database_names()
    return sorted(n.removeprefix(prefix) for n in names if n.startswith(prefix))


def run_full_pipeline_for_tenant(tenant_id: str) -> dict:
    """Full refresh for one tenant. Tenant-scoped and best-effort.

    Ingests every configured connector, then (only if at least one ran) runs the
    post-ingest pipeline and refreshes the in-process search index. Returns a
    small summary dict for logging/telemetry.
    """
    tenancy.set_tenant(tenant_id)
    ran = [name for name in cc.SUPPORTED_CONNECTORS if _ingest_connector(name)]
    if not ran:
        log.info("tenant=%s has no configured connectors; skipping pipeline", tenant_id)
        return {"tenant": tenant_id, "connectors": [], "embedded": False}

    _run_post_ingest_pipeline()
    invalidate_index(tenant_id)
    log.info("tenant=%s refresh complete connectors=%s", tenant_id, ran)
    return {"tenant": tenant_id, "connectors": ran, "embedded": True}


def run_all_tenants() -> None:
    """Run the full refresh pipeline for every tenant, one at a time.

    Sequential by design — bounds load on the upstream APIs and OpenAI. A crash
    in one tenant's pipeline is logged and the run continues. This is the
    callable the background scheduler fires on an interval.
    """
    tenants = list_tenant_ids()
    log.info("auto-sync starting for %d tenant(s)", len(tenants))
    for tenant_id in tenants:
        try:
            run_full_pipeline_for_tenant(tenant_id)
        except Exception:
            log.exception("auto-sync pipeline crashed for tenant=%s", tenant_id)
    log.info("auto-sync run complete")
