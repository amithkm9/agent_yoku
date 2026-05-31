"""Unit tests for the auto-sync scheduler wiring.

`BackgroundScheduler` is replaced with a fake so no real threads start.
"""

from __future__ import annotations

import pytest

from yoku import scheduler as sched_mod
from yoku import sync_service


class FakeScheduler:
    def __init__(self, timezone=None):
        self.timezone = timezone
        self.jobs: list = []
        self.started = False
        self.stopped = False

    def add_job(self, func, **kwargs):
        self.jobs.append((func, kwargs))

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.stopped = True


@pytest.fixture
def fresh_scheduler(monkeypatch):
    """Isolate the module-level singleton + swap in the fake scheduler."""
    monkeypatch.setattr(sched_mod, "_scheduler", None)
    monkeypatch.setattr(sched_mod, "BackgroundScheduler", FakeScheduler)
    yield
    sched_mod.shutdown()


def test_start_is_idempotent_and_registers_one_job(monkeypatch, fresh_scheduler):
    monkeypatch.setattr(sched_mod.settings, "sync_interval_minutes", 30)

    first = sched_mod.start()
    second = sched_mod.start()

    assert first is second  # one scheduler per process
    assert first.started is True
    assert len(first.jobs) == 1

    func, kwargs = first.jobs[0]
    assert func is sync_service.run_all_tenants
    assert kwargs["minutes"] == 30
    assert kwargs["max_instances"] == 1
    assert kwargs["coalesce"] is True


def test_interval_clamped_to_at_least_one_minute(monkeypatch, fresh_scheduler):
    monkeypatch.setattr(sched_mod.settings, "sync_interval_minutes", 0)
    scheduler = sched_mod.start()
    assert scheduler.jobs[0][1]["minutes"] == 1


def test_shutdown_stops_and_clears_singleton(monkeypatch, fresh_scheduler):
    scheduler = sched_mod.start()
    sched_mod.shutdown()
    assert scheduler.stopped is True
    assert sched_mod._scheduler is None


def test_shutdown_without_start_is_noop(fresh_scheduler):
    sched_mod.shutdown()  # must not raise when nothing is running
