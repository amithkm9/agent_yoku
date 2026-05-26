"""JIRA connector — pulls AS-project tickets + users from Atlassian Cloud."""

from agent_yoku.connectors.base import ConnectorMeta

META: ConnectorMeta = {
    "name": "jira",
    "source": "Atlassian JIRA",
    "description": "AS-project tickets and the user directory.",
    "entity": "tickets",
}
