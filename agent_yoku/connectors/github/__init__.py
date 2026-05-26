"""GitHub connector — pulls AsatoCorp PRs + org members."""

from agent_yoku.connectors.base import ConnectorMeta

META: ConnectorMeta = {
    "name": "github",
    "source": "GitHub",
    "description": "AsatoCorp pull requests (last N days) and org members.",
    "entity": "pull_requests",
}
