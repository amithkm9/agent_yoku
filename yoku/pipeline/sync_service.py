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

import gc
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from yoku.agent.tools import invalidate_index
from yoku.config import settings
from yoku.connectors._runtime import (
    github_config_from_dict,
    jira_config_from_dict,
    slack_config_from_dict,
    use_github,
    use_jira,
    use_slack,
)
from yoku.db import connector_configs as cc
from yoku.db import tenancy
from yoku.logging import get_logger

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
        from yoku.connectors.jira import ingest as ingest_mod
        from yoku.connectors.jira import users_ingest as users_mod

        ingest_mod.main()
        users_mod.main()


def _ingest_github(decrypted: dict) -> None:
    with use_github(github_config_from_dict(decrypted)):
        from yoku.connectors.github import ingest as ingest_mod
        from yoku.connectors.github import users_ingest as users_mod

        ingest_mod.main()
        users_mod.main()


def _ingest_slack(decrypted: dict) -> None:
    with use_slack(slack_config_from_dict(decrypted)):
        from yoku.connectors.slack import ingest as ingest_mod
        from yoku.connectors.slack import users_ingest as users_mod

        ingest_mod.main()
        users_mod.main()


_INGEST_FNS: dict[str, Callable[[dict], None]] = {
    "jira": _ingest_jira,
    "github": _ingest_github,
    "slack": _ingest_slack,
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


def _best_effort(step: str, fn: Callable[[], object]) -> None:
    """Run a post-ingest projection step, logging (not raising) on failure."""
    try:
        fn()
    except Exception:
        log.exception("post-ingest step %r failed for tenant=%s", step, tenancy.current_tenant())


def _run_post_ingest_pipeline() -> None:
    """Embed deltas, unify users, link PRs to JIRA, backfill author emails,
    then project everything into the canonical `documents` collection.

    Operates on whichever tenant is currently bound. Same order as the
    `refresh-all` CLI.
    """
    from yoku.db import backfill, unified_users
    from yoku.pipeline import embed as embed_mod
    from yoku.pipeline import pr_to_jira
    from yoku.pipeline.entity_links import build_entity_links
    from yoku.pipeline.slack_threads import rollup_threads
    from yoku.pipeline.unify import unify_all

    # Thread rollup must precede embed so re-rolled parents re-embed this pass.
    _best_effort("slack_threads", rollup_threads)
    with _clean_argv("embed"):
        embed_mod.main()
    unified_users.main()
    with _clean_argv("link"):
        pr_to_jira.main()
    backfill.main()
    from yoku.proactive.signals import run_detectors

    # Canonical projection (Way B) is best-effort: a failure here must not
    # discard the core refresh that already succeeded above.
    _best_effort("unify", unify_all)
    _best_effort("entity_links", build_entity_links)
    from yoku.pipeline.metrics import compute_metrics
    from yoku.proactive.baselines import compute_baselines
    from yoku.proactive.beliefs import consolidate_beliefs
    from yoku.proactive.judge import judge_signals
    from yoku.proactive.orchestrator import run_proactive_loop
    from yoku.proactive.reply_understanding import understand_replies

    # Proactive stages close the pipeline — they read what everything above
    # just wrote. Baselines and consolidated beliefs feed the judge's person
    # dimension; the judge runs over what detectors found; the speak-first loop
    # shadows/sends for judged-real gaps and threads inbound replies; reply
    # understanding then reads those replies to suppress/learn; metrics come
    # last so `signals_open` reflects this run.
    _best_effort("baselines", compute_baselines)
    _best_effort("beliefs", consolidate_beliefs)
    _best_effort("detectors", run_detectors)
    _best_effort("judge", judge_signals)
    _best_effort("proactive_loop", run_proactive_loop)
    _best_effort("reply_understanding", understand_replies)
    _best_effort("metrics", compute_metrics)


def list_tenant_ids() -> list[str]:
    """Every tenant that has its own mongo database.

    Tenant dbs are named ``<mongo_db>_<tenant_id>`` (see
    `tenancy.tenant_db_name`). Valid tenant ids contain only url-safe chars, so
    the suffix round-trips back to the tenant id unchanged. Mongo's own
    `admin`/`config`/`local` dbs and the unsuffixed base db are excluded.
    """
    from yoku.db.mongo import _client

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
        finally:
            gc.collect()
    log.info("auto-sync run complete")
