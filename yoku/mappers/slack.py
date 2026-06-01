"""Map a stored Slack message doc into the typed `Conversation` primitive."""

from __future__ import annotations

from yoku.constants import Primitive
from yoku.mappers.base import canonical_key, ref
from yoku.schemas import Conversation

PROVIDER = "slack"


def map_slack_message(doc: dict) -> Conversation:
    """Slack message -> Conversation(domain=conversation)."""
    text = doc.get("text")
    return Conversation(
        key=canonical_key(PROVIDER, doc["key"]),
        provider=PROVIDER,
        domain=Primitive.CONVERSATION,
        type="message",
        title=doc.get("summary"),
        body=text,
        author=doc.get("author_name"),
        status=None,
        url=doc.get("url"),
        created=doc.get("created") or doc.get("ts"),
        updated=doc.get("updated") or doc.get("ts"),
        refs=[ref("jira", k) for k in doc.get("jira_keys") or []],
        channel=doc.get("channel_name"),
        channel_id=doc.get("channel_id"),
        channel_type="channel",
        thread_ts=doc.get("thread_ts"),
        reply_count=doc.get("reply_count", 0),
        text=text,
    )
