"""`yoku doctor` health checks — hermetic via CliRunner + mocked externals."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from yoku import cli as cli_mod


@pytest.fixture
def all_healthy(monkeypatch):
    """Stub every external the doctor checks so all four pass."""
    import yoku.core.storage.connector_configs as cc
    import yoku.core.storage.freshness as freshness
    import yoku.core.storage.mongo as mongo
    from yoku.core.config import Settings

    monkeypatch.setattr(mongo, "ping", lambda: None)
    monkeypatch.setattr(cc, "list_configs", lambda: [{"name": "jira"}])
    monkeypatch.setattr(
        freshness,
        "source_freshness",
        lambda: [
            {
                "source": "jira",
                "last_synced_at": "2026-05-30T00:00:00+00:00",
                "synced_ago": "1 day ago",
            },
            {
                "source": "github",
                "last_synced_at": "2026-05-31T00:00:00+00:00",
                "synced_ago": "just now",
            },
        ],
    )
    # The test env ships the default dev JWT secret; treat it as overridden here.
    monkeypatch.setattr(Settings, "is_default_jwt_secret", property(lambda self: False))


@pytest.mark.unit
def test_doctor_all_pass(all_healthy):
    result = CliRunner().invoke(cli_mod.cli, ["doctor"])
    assert result.exit_code == 0
    assert result.output.count("[PASS]") == 4
    assert "[FAIL]" not in result.output


@pytest.mark.unit
def test_doctor_exits_nonzero_when_mongo_down(all_healthy, monkeypatch):
    import yoku.core.storage.mongo as mongo

    def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mongo, "ping", _boom)
    result = CliRunner().invoke(cli_mod.cli, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] mongodb" in result.output


@pytest.mark.unit
def test_doctor_flags_default_jwt_secret(all_healthy, monkeypatch):
    from yoku.core.config import Settings

    monkeypatch.setattr(Settings, "is_default_jwt_secret", property(lambda self: True))
    result = CliRunner().invoke(cli_mod.cli, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] secrets" in result.output


@pytest.mark.unit
def test_doctor_reports_never_synced(all_healthy, monkeypatch):
    import yoku.core.storage.freshness as freshness

    monkeypatch.setattr(
        freshness,
        "source_freshness",
        lambda: [{"source": "github", "last_synced_at": None, "synced_ago": "never"}],
    )
    result = CliRunner().invoke(cli_mod.cli, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] freshness" in result.output
    assert "github" in result.output
