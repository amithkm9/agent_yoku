"""JIRA connector — pulls AS-project tickets + users from Atlassian Cloud."""

from agent_yoku.connectors.base import ConnectorMeta

META: ConnectorMeta = {
    "name": "jira",
    "source": "Atlassian JIRA",
    "display_name": "JIRA",
    "description": "AS-project tickets and the user directory.",
    "entity": "tickets",
    "sync_summary": "Pulls issues from one JIRA project plus the tenant's user directory.",
    "setup_steps": [
        "Use a JIRA account that can read the target project.",
        "Create an Atlassian API token for that account.",
        "Enter the exact project key that agent_yoku should index.",
        "Run the first sync after saving to populate tickets and users.",
    ],
    "field_help": {
        "base_url": "Your Atlassian site URL, for example https://your-org.atlassian.net",
        "email": "The email address tied to the Atlassian account that owns the API token.",
        "project": "The JIRA project key to index, for example AS.",
        "token": "Atlassian API token. Stored encrypted and never returned to the UI.",
    },
}
