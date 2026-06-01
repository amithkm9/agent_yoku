"""Map a stored JIRA ticket doc into the typed `WorkItem` primitive."""

from __future__ import annotations

from yoku.constants import Primitive
from yoku.mappers.base import canonical_key, embed_text, ref
from yoku.schemas import WorkItem

PROVIDER = "jira"


def _pr_ref(item: object) -> str | None:
    """A linked_prs entry is a LinkedPR dict (has 'key') or a bare key string."""
    key = item.get("key") if isinstance(item, dict) else item
    return ref("github", key) if key else None


def map_jira_ticket(doc: dict) -> WorkItem:
    """JIRA ticket -> WorkItem(domain=work_item)."""
    title = doc.get("summary")
    body = doc.get("description")
    refs = [r for r in (_pr_ref(p) for p in doc.get("linked_prs") or []) if r]
    return WorkItem(
        key=canonical_key(PROVIDER, doc["key"]),
        provider=PROVIDER,
        domain=Primitive.WORK_ITEM,
        type="ticket",
        title=title,
        body=body,
        author=doc.get("assignee"),
        status=doc.get("status"),
        url=doc.get("url"),
        created=doc.get("created"),
        updated=doc.get("updated"),
        refs=refs,
        priority=doc.get("priority"),
        issue_type=doc.get("issuetype"),
        epic_key=doc.get("epic_key"),
        parent_key=doc.get("parent_key"),
        reporter=doc.get("reporter"),
        labels=doc.get("labels") or [],
        resolved=doc.get("resolved"),
        text=doc.get("text") or embed_text(title, body),
    )
