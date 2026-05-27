"""Background auto-sync scheduler.

Fires `sync_service.run_all_tenants` on a fixed interval so each tenant's JIRA +
GitHub data refreshes without anyone clicking "sync".

Uses APScheduler's `BackgroundScheduler` (its own thread pool) rather than the
asyncio scheduler: the refresh pipeline is blocking (network ingest + OpenAI
embeds), so it must run off the API event loop.

Lifecycle is owned by the FastAPI lifespan in `agent_yoku.main`; nothing else
should start or stop it.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from agent_yoku import sync_service
from agent_yoku.config import settings
from agent_yoku.log import get_logger

log = get_logger("scheduler")

_JOB_ID = "auto-sync-all-tenants"

_scheduler: BackgroundScheduler | None = None


def start() -> BackgroundScheduler:
    """Start the auto-sync scheduler. Idempotent within a process.

    The first run fires one interval after start (not at boot) so server
    startup isn't blocked by a full ingest. Overlapping runs are prevented
    (`max_instances=1`) and missed runs collapse into one (`coalesce=True`).
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    interval = max(1, settings.sync_interval_minutes)
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        sync_service.run_all_tenants,
        trigger="interval",
        minutes=interval,
        id=_JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    log.info("auto-sync scheduler started — every %d min", interval)
    return scheduler


def shutdown() -> None:
    """Stop the scheduler if running. Safe to call when never started."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("auto-sync scheduler stopped")
