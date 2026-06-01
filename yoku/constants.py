"""Central place for application-level constants.

Operational limits, domain sets, and other non-env-driven values that are
referenced across multiple modules or are likely to need tuning.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

#: Docs sent to OpenAI in a single embeddings.create call.
EMBED_BATCH_SIZE = 64

#: Characters kept from each doc's `text` field before embedding.
EMBED_MAX_CHARS = 8_000

#: Cost per million tokens for text-embedding-3-small (used for log estimates).
EMBED_COST_PER_M_TOKENS = 0.02

#: Collections that carry an `embedding` vector and need periodic embedding runs.
EMBEDDABLE_COLLECTIONS = ("dc-jira", "dc-github", "dc-slack")

# ---------------------------------------------------------------------------
# Ingest / linking
# ---------------------------------------------------------------------------

#: GitHub PR docs flushed to mongo in one bulk_write call.
INGEST_BATCH_SIZE = 100

#: Ops flushed to mongo in one bulk_write call during PR → JIRA linking.
LINK_BATCH_SIZE = 200

# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

#: All connector names the platform supports.
SUPPORTED_CONNECTORS: tuple[str, ...] = ("jira", "github", "slack")

# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------

#: JIRA statuses that assert work is finished.
#: A ticket in one of these states with no linked PR is flagged as suspicious.
DEFAULT_DONE_STATUSES: frozenset[str] = frozenset(
    {
        "Done",
        "Closed",
        "Dev Complete",
        "QA Approved",
        "Accepted for delivery",
        "Moved to UAT",
    }
)


class Primitive(StrEnum):
    """The canonical primitive kinds the agent reasons over (the `domain` field)."""

    WORK_ITEM = "work_item"
    PULL_REQUEST = "pull_request"
    CONVERSATION = "conversation"
    DOCUMENT = "document"
    EVENT = "event"
    PERSON = "person"
