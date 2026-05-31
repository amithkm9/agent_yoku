"""`data_freshness` — per-source counts + last sync time."""

from __future__ import annotations

from langchain_core.tools import tool

from yoku.core.storage.freshness import source_freshness


@tool
def data_freshness() -> list[dict]:
    """Report how current each data source is: document count, when it last
    synced, the relative age, and the last sync status.

    Call this before concluding a source has no data — an empty result can mean
    the source is unsynced or stale, not that the work doesn't exist. Surface
    the age in your answer (e.g. "no GitHub data — last synced 6 days ago")
    rather than guessing.
    """
    return source_freshness()
