"""GitHub connector — pulls AsatoCorp PRs + org members."""

from yoku.connectors.base import ConnectorMeta

META: ConnectorMeta = {
    "name": "github",
    "source": "GitHub",
    "display_name": "GitHub",
    "description": "AsatoCorp pull requests (last N days) and org members.",
    "entity": "pull_requests",
    "sync_summary": "Pulls pull requests across the configured org and ingests GitHub users.",
    "setup_steps": [
        "Create a GitHub personal access token with org and repo read access.",
        "Enter the GitHub organization name that yoku should index.",
        "Set a PR lookback window that matches how much history your team needs.",
        "Run the first sync after saving to populate PRs and contributors.",
    ],
    "field_help": {
        "api_base": "Use the public GitHub API unless you run GitHub Enterprise.",
        "org": "Just the org slug from the URL (e.g. 'AsatoCorp'), not the full link.",
        "pr_lookback_days": "How many days of pull request history to ingest on sync.",
        "token": "GitHub personal access token. Stored encrypted and never returned to the UI.",
    },
}
