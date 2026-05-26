"""Shared connector contract.

A `Connector` knows how to pull one external source (JIRA, GitHub, …) into
mongo. Each concrete connector lives in its own subpackage under
`agent_yoku/connectors/<name>/` with at minimum:

    client.py        # auth + paginated REST helpers (decorated with tenacity)
    ingest.py        # `def main(): …` — primary entity ingest
    users_ingest.py  # optional — users/members ingest

The CLI auto-discovers connectors by name; adding a new source = copy one of
the existing folders and adapt. There's no formal class hierarchy because the
work is mostly module-level functions, but every connector should advertise
itself via a module-level `META` dict so the CLI can list them.
"""

from __future__ import annotations

from typing import Protocol, TypedDict


class ConnectorMeta(TypedDict, total=False):
    name: str  # short identifier, e.g. "jira", "github"
    source: str  # external system human name
    description: str  # one-line
    entity: str  # primary entity, e.g. "tickets", "pull_requests"


class IngestModule(Protocol):
    """A module that exposes a no-arg `main()` to run an ingest."""

    META: ConnectorMeta

    def main(self) -> None:  # pragma: no cover — protocol shape
        ...


def import_connector(name: str) -> dict[str, IngestModule]:
    """Lazily import a connector's submodules.

    Returns a dict {"ingest": <module>, "users_ingest": <module|None>}.
    Raises ImportError if the connector subpackage doesn't exist.
    """
    import importlib

    pkg = f"agent_yoku.connectors.{name}"
    ingest = importlib.import_module(f"{pkg}.ingest")
    try:
        users_ingest = importlib.import_module(f"{pkg}.users_ingest")
    except ImportError:
        users_ingest = None
    return {"ingest": ingest, "users_ingest": users_ingest}


def list_connectors() -> list[ConnectorMeta]:
    """Enumerate all registered connectors. Each connector advertises itself
    via a `META` dict in its `__init__.py`.
    """
    import importlib
    import pkgutil

    import agent_yoku.connectors as pkg

    out: list[ConnectorMeta] = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.ispkg:
            continue
        try:
            sub = importlib.import_module(f"agent_yoku.connectors.{info.name}")
        except ImportError:
            continue
        meta = getattr(sub, "META", None)
        if meta:
            out.append(meta)
    return out
