"""Slack connector — pulls public channel messages + workspace users."""

from yoku.connectors.base import ConnectorMeta

META: ConnectorMeta = {
    "name": "slack",
    "source": "Slack",
    "display_name": "Slack",
    "description": "Public channel messages and workspace user directory.",
    "entity": "messages",
    "sync_summary": "Pulls messages from public channels within the configured lookback window.",
    "setup_steps": [
        "Create a Slack app at api.slack.com/apps and install it to your workspace.",
        "Add bot token scopes: channels:history, channels:read, users:read, users:read.email.",
        "Copy the Bot User OAuth Token (starts with xoxb-).",
        "Enter your workspace slug (the part before .slack.com in your Slack URL).",
        "Run the first sync after saving to populate messages.",
    ],
    "field_help": {
        "bot_token": "Bot User OAuth Token starting with xoxb-. Stored encrypted.",
        "workspace": "Your Slack workspace slug, e.g. 'acme' for acme.slack.com.",
        "lookback_days": "How many days of message history to ingest. Default: 90.",
        "channel_types": "Comma-separated channel types to ingest. Default: public_channel.",
    },
}
