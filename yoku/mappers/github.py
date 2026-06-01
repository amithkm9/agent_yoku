"""Map a stored GitHub PR doc into the typed `PullRequest` primitive."""

from __future__ import annotations

from yoku.mappers.base import canonical_key, embed_text, ref
from yoku.schemas import Primitive, PullRequest

PROVIDER = "github"


def map_github_pr(doc: dict) -> PullRequest:
    """GitHub PR -> PullRequest(domain=pull_request)."""
    title = doc.get("summary")
    body = doc.get("description")
    return PullRequest(
        key=canonical_key(PROVIDER, doc["key"]),
        provider=PROVIDER,
        domain=Primitive.PULL_REQUEST,
        type="pull_request",
        title=title,
        body=body,
        author=doc.get("author"),
        status=doc.get("status"),
        url=doc.get("url"),
        created=doc.get("created"),
        updated=doc.get("updated"),
        refs=[ref("jira", k) for k in doc.get("jira_keys") or []],
        repo=doc.get("repo"),
        number=doc.get("number"),
        merged=bool(doc.get("merged", False)),
        is_draft=bool(doc.get("is_draft", False)),
        base=doc.get("base"),
        head=doc.get("head"),
        merged_at=doc.get("merged_at"),
        comments_count=doc.get("comments_count", 0),
        labels=doc.get("labels") or [],
        text=doc.get("text") or embed_text(title, body),
    )
