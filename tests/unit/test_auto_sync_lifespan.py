"""The FastAPI lifespan starts auto-sync only when enabled and outside test/ci."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_yoku import scheduler as sched_mod
from agent_yoku.config import settings
from agent_yoku.main import create_app


@pytest.fixture
def capture_scheduler(monkeypatch):
    """Record start/stop calls instead of spinning a real scheduler."""
    calls: list[str] = []
    monkeypatch.setattr(sched_mod, "start", lambda: calls.append("start"))
    monkeypatch.setattr(sched_mod, "shutdown", lambda: calls.append("shutdown"))
    return calls


def test_lifespan_starts_autosync_when_enabled_locally(monkeypatch, capture_scheduler):
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setattr(settings, "auto_sync_enabled", True)

    with TestClient(create_app()):
        pass

    assert "start" in capture_scheduler
    assert "shutdown" in capture_scheduler  # cleaned up on exit


@pytest.mark.parametrize("env", ["test", "ci"])
def test_lifespan_skips_autosync_in_test_and_ci(monkeypatch, capture_scheduler, env):
    monkeypatch.setenv("ENV", env)
    monkeypatch.setattr(settings, "auto_sync_enabled", True)

    with TestClient(create_app()):
        pass

    assert "start" not in capture_scheduler


def test_lifespan_skips_autosync_when_disabled(monkeypatch, capture_scheduler):
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setattr(settings, "auto_sync_enabled", False)

    with TestClient(create_app()):
        pass

    assert "start" not in capture_scheduler
