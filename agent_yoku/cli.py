"""Unified agent_yoku CLI.

Single Click entry point for all maintenance + dev tasks. Each subcommand
delegates to a module's main() — the underlying scripts also remain runnable
standalone via `python -m agent_yoku.connectors.jira.ingest`.

Usage:
    agent-yoku --help
    agent-yoku ingest jira
    agent-yoku refresh-all
    agent-yoku chat "what's the status of long-term memory?"
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import click

_HERE = Path(__file__).resolve().parent


def _normalize_cli_tenant(tenant: str) -> str:
    from agent_yoku.storage import tenancy

    normalized = tenancy.normalize_tenant_id(tenant)
    if not tenancy.is_valid_tenant_id(normalized):
        raise click.BadParameter(
            "tenant must be 2-40 chars of a-z0-9, '-' or '_', starting alphanumeric",
            param_hint="--tenant",
        )
    return normalized


@click.group()
@click.option("--tenant", default=None, help="Tenant id for commands that access tenant data.")
@click.option("--log-level", default=None, help="Override LOG_LEVEL (DEBUG/INFO/WARNING/ERROR)")
def cli(tenant: str | None, log_level: str | None) -> None:
    """agent_yoku — JIRA + GitHub deepagent."""
    if tenant:
        from agent_yoku.storage import tenancy

        tenancy.set_tenant(_normalize_cli_tenant(tenant))
    if log_level:
        os.environ["LOG_LEVEL"] = log_level.upper()


# =================== INGEST ===================


@cli.group()
def ingest() -> None:
    """Pull source data into mongo."""


@ingest.command("jira")
@click.argument("extra_jql", required=False)
def ingest_jira(extra_jql: str | None) -> None:
    """Pull all (or filtered) JIRA tickets from project AS."""
    from agent_yoku.connectors.jira import ingest as mod

    sys.argv = ["ingest", extra_jql] if extra_jql else ["ingest"]
    mod.main()


@ingest.command("jira-users")
def ingest_jira_users() -> None:
    """Pull JIRA users directory."""
    from agent_yoku.connectors.jira import users_ingest

    users_ingest.main()


@ingest.command("github")
@click.argument("repos", nargs=-1)
def ingest_github(repos: tuple[str, ...]) -> None:
    """Pull GitHub PRs (no args = all non-archived AsatoCorp repos)."""
    from agent_yoku.connectors.github import ingest as mod

    sys.argv = ["ingest_github", *repos]
    mod.main()


@ingest.command("github-users")
def ingest_github_users_cmd() -> None:
    """Pull AsatoCorp org members + profiles."""
    from agent_yoku.connectors.github import users_ingest

    users_ingest.main()


# =================== ENRICH ===================


@cli.command()
@click.option(
    "--coll",
    type=click.Choice(["jira_tickets", "github_prs"]),
    default=None,
    help="Single collection to embed; omit to embed both.",
)
@click.option("--all", "re_embed_all", is_flag=True, help="Re-embed everything, not just missing.")
def embed(coll: str | None, re_embed_all: bool) -> None:
    """Generate embeddings for any docs with embedding=null."""
    from agent_yoku.embeddings import embed as mod

    sys.argv = ["embed"]
    if coll:
        sys.argv += ["--coll", coll]
    if re_embed_all:
        sys.argv += ["--all"]
    mod.main()


@cli.command()
@click.option("--reset", is_flag=True, help="Clear linked_prs on JIRA tickets first.")
def link(reset: bool) -> None:
    """Reverse-link PRs onto their referenced JIRA tickets."""
    from agent_yoku.linking import pr_to_jira

    sys.argv = ["link_prs_to_jira"] + (["--reset"] if reset else [])
    pr_to_jira.main()


@cli.command("unify-users")
def unify_users() -> None:
    """Build unified_users by joining JIRA + GitHub users."""
    from agent_yoku.storage import unified_users as mod

    mod.main()


@cli.command("backfill-pr-emails")
def backfill_pr_emails() -> None:
    """Backfill author_email / assignee_email on PR docs from unified_users."""
    from agent_yoku.storage import backfill

    backfill.main()


# =================== END-TO-END ===================


@cli.command("refresh-all")
@click.option("--skip-jira", is_flag=True)
@click.option("--skip-github", is_flag=True)
def refresh_all(skip_jira: bool, skip_github: bool) -> None:
    """Full refresh: ingest -> embed -> unify -> link -> backfill."""
    if not skip_jira:
        click.secho("→ ingest jira", fg="cyan")
        sys.argv = ["ingest"]
        from agent_yoku.connectors.jira import ingest as jira_ingest

        jira_ingest.main()
        click.secho("→ ingest jira-users", fg="cyan")
        from agent_yoku.connectors.jira import users_ingest as jira_users

        jira_users.main()
    if not skip_github:
        click.secho("→ ingest github", fg="cyan")
        sys.argv = ["ingest_github"]
        from agent_yoku.connectors.github import ingest as gh_ingest

        gh_ingest.main()
        click.secho("→ ingest github-users", fg="cyan")
        from agent_yoku.connectors.github import users_ingest as gh_users

        gh_users.main()
    click.secho("→ embed (deltas only)", fg="cyan")
    sys.argv = ["embed"]
    from agent_yoku.embeddings import embed as _embed

    _embed.main()
    click.secho("→ unify-users", fg="cyan")
    from agent_yoku.storage import unified_users as _unified

    _unified.main()
    click.secho("→ link prs -> jira", fg="cyan")
    sys.argv = ["link_prs_to_jira"]
    from agent_yoku.linking import pr_to_jira

    pr_to_jira.main()
    click.secho("→ backfill pr emails", fg="cyan")
    from agent_yoku.storage import backfill

    backfill.main()
    click.secho("✓ refresh-all done", fg="green")


# =================== ASK ===================


@cli.command()
@click.argument("query", nargs=-1, required=True)
def chat(query: tuple[str, ...]) -> None:
    """One-shot agent query (CLI). Use `ui` for the interactive Streamlit app."""
    from agent_yoku.agent.chat import ask, final_answer
    from agent_yoku.log import set_session

    # Bind a session_id so all logs from this run correlate.
    set_session(str(uuid.uuid4()))

    q = " ".join(query)
    result = ask(q)
    click.echo(final_answer(result))


@cli.command()
@click.option("--port", default=8000, type=int)
@click.option("--host", default="127.0.0.1")
@click.option("--reload/--no-reload", default=False)
def api(port: int, host: str, reload: bool) -> None:
    """Launch the FastAPI server (auth + sessions + chat + stats)."""
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "agent_yoku.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        cmd.append("--reload")
    os.execvp(cmd[0], cmd)


@cli.command()
@click.option("--port", default=8501, type=int)
@click.option("--headless/--browser", default=True)
def ui(port: int, headless: bool) -> None:
    """Launch the legacy Streamlit deepagent UI (use `web` for the React UI)."""
    app_path = _HERE / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
    ]
    if headless:
        cmd += ["--server.headless", "true"]
    os.execvp(cmd[0], cmd)


# =================== AUTH ===================


@cli.group()
def auth() -> None:
    """User management — bootstrap admins, list/delete accounts."""


@auth.command("create-user")
@click.option("--email", required=True)
@click.option("--password", required=True, hide_input=True, prompt=True, confirmation_prompt=True)
@click.option("--name", default=None)
@click.option("--tenant", required=True, help="Tenant id")
@click.option("--admin/--no-admin", default=False)
def auth_create_user(
    email: str, password: str, name: str | None, tenant: str | None, admin: bool
) -> None:
    """Create a user in the named tenant's auth_users collection."""
    import uuid
    from datetime import UTC
    from datetime import datetime as _dt

    from agent_yoku.deps import hash_password
    from agent_yoku.storage import tenancy
    from agent_yoku.storage.mongo import auth_users_collection

    tenancy.set_tenant(_normalize_cli_tenant(tenant))
    coll = auth_users_collection()
    if coll.find_one({"email": email.lower()}):
        click.secho(
            f"user {email!r} already exists in tenant {tenancy.current_tenant()!r}", fg="red"
        )
        return
    doc = {
        "user_id": str(uuid.uuid4()),
        "email": email.lower(),
        "name": name,
        "password_hash": hash_password(password),
        "tenant_id": tenancy.current_tenant(),
        "is_admin": admin or coll.count_documents({}) == 0,
        "created_at": _dt.now(UTC),
    }
    coll.insert_one(doc)
    click.secho(
        f"created user {email!r} in tenant {doc['tenant_id']!r} " f"(admin={doc['is_admin']})",
        fg="green",
    )


@auth.command("list-users")
@click.option("--tenant", required=True)
def auth_list_users(tenant: str | None) -> None:
    """List users in the named tenant."""
    from agent_yoku.storage import tenancy
    from agent_yoku.storage.mongo import auth_users_collection

    tenancy.set_tenant(_normalize_cli_tenant(tenant))
    for u in auth_users_collection().find({}, {"_id": 0, "password_hash": 0}):
        click.echo(
            f"  {u.get('email'):30s}  admin={u.get('is_admin'):<5}  "
            f"tenant={u.get('tenant_id')}  name={u.get('name')}"
        )


# =================== STATS ===================


@cli.command("list-connectors")
def list_connectors_cmd() -> None:
    """List all registered data connectors."""
    from agent_yoku.connectors.base import list_connectors

    for meta in list_connectors():
        click.echo(
            f"  {meta.get('name', '?'):10s}  {meta.get('source', '?'):20s}  "
            f"{meta.get('description', '')}"
        )


@cli.command()
def status() -> None:
    """Print mongo collection counts."""
    from agent_yoku.storage.mongo import (
        chat_messages_collection,
        chat_sessions_collection,
        github_prs_collection,
        github_users_collection,
        tickets_collection,
        unified_users_collection,
        users_collection,
    )

    pairs = [
        ("jira_tickets", tickets_collection),
        ("jira users", users_collection),
        ("github_prs", github_prs_collection),
        ("github_users", github_users_collection),
        ("unified_users", unified_users_collection),
        ("chat_sessions", chat_sessions_collection),
        ("chat_messages", chat_messages_collection),
    ]
    for label, fn in pairs:
        n = fn().count_documents({})
        click.echo(f"  {label:18s} {n:>8d}")


if __name__ == "__main__":
    cli()
