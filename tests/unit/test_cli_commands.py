"""CLI wiring tests — each subcommand delegates to a mocked side effect.

Uses Click's CliRunner; the real ingest / embed / agent modules are stubbed so
no network, mongo, or OpenAI calls happen.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from yoku.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.unit
def test_root_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "deepagent" in result.output.lower() or "jira" in result.output.lower()


@pytest.mark.unit
def test_invalid_tenant_rejected(runner):
    result = runner.invoke(cli, ["--tenant", "Has Space", "status"])
    assert result.exit_code != 0
    assert "tenant" in result.output.lower()


@pytest.mark.unit
def test_chat_command_delegates(runner, monkeypatch):
    import yoku.agent.chat as chat_mod

    monkeypatch.setattr(chat_mod, "ask", lambda q: {"messages": []})
    monkeypatch.setattr(chat_mod, "final_answer", lambda r: "mocked answer")

    result = runner.invoke(cli, ["chat", "what", "is", "up"])
    assert result.exit_code == 0
    assert "mocked answer" in result.output


@pytest.mark.unit
def test_ingest_jira_delegates(runner, monkeypatch):
    called = {}
    from yoku.pipeline.connectors.jira import ingest as jira_ingest

    monkeypatch.setattr(jira_ingest, "main", lambda extra=None: called.setdefault("extra", extra))

    result = runner.invoke(cli, ["ingest", "jira"])
    assert result.exit_code == 0, result.output
    assert "extra" in called


@pytest.mark.unit
def test_embed_command_delegates(runner, monkeypatch):
    from yoku.pipeline import embed as embed_mod

    monkeypatch.setattr(embed_mod, "main", lambda: None)

    result = runner.invoke(cli, ["embed", "--coll", "dc-jira"])
    assert result.exit_code == 0, result.output


@pytest.mark.unit
def test_link_command_delegates(runner, monkeypatch):
    from yoku.pipeline import pr_to_jira

    monkeypatch.setattr(pr_to_jira, "main", lambda: None)

    result = runner.invoke(cli, ["link"])
    assert result.exit_code == 0, result.output


@pytest.mark.unit
def test_unify_users_delegates(runner, monkeypatch):
    from yoku.core.storage import unified_users

    monkeypatch.setattr(unified_users, "main", lambda: None)

    result = runner.invoke(cli, ["unify-users"])
    assert result.exit_code == 0, result.output


@pytest.mark.unit
def test_list_connectors_delegates(runner, monkeypatch):
    from yoku.pipeline.connectors import base

    monkeypatch.setattr(
        base,
        "list_connectors",
        lambda: [{"name": "jira", "source": "JIRA", "description": "tickets"}],
    )

    result = runner.invoke(cli, ["list-connectors"])
    assert result.exit_code == 0, result.output
    assert "jira" in result.output


@pytest.mark.unit
def test_consistency_command_renders_report(runner, monkeypatch):
    import yoku.pipeline.consistency as consistency_mod

    monkeypatch.setattr(
        consistency_mod,
        "consistency_report",
        lambda sample=10: {
            "done_without_pr": {"count": 1, "sample": ["AS-1"]},
            "merged_without_ticket": {"count": 0, "sample": []},
        },
    )

    result = runner.invoke(cli, ["consistency"])
    assert result.exit_code == 0, result.output
    assert "AS-1" in result.output
