"""Shared pytest fixtures.

Unit tests should be hermetic — no network, no mongo. Integration tests
target a scratch mongo db that gets cleaned per session.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("JIRA_EMAIL", "test@example.com")
os.environ.setdefault("JIRA_TOKEN", "test-token")
os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def scratch_db_name() -> str:
    """Random per-session mongo db name so integration tests don't trample data."""
    return f"yoku_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def scratch_db(scratch_db_name):
    """Yield a temp mongo db handle; drop it on teardown.

    Skips the dependent tests if mongo is not reachable on localhost.
    """
    pymongo = pytest.importorskip("pymongo")
    client = pymongo.MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except Exception:
        if os.getenv("CI"):
            pytest.fail("Mongo is required in CI but was not reachable on localhost:27017")
        pytest.skip("local mongo not available")
    db = client[scratch_db_name]
    yield db
    client.drop_database(scratch_db_name)


@pytest.fixture
def isolated_index(monkeypatch):
    """Reset the per-tenant index cache + tenant context between tests.

    Also disables the ZeroEntropy reranker so search stays hermetic — tests that
    exercise the rerank path stub `tools.rerank` themselves.
    """
    from yoku.agent import tools
    from yoku.core.storage import tenancy

    monkeypatch.setattr(tools, "_INDEXES", {})
    monkeypatch.setattr(tools, "rerank", lambda *a, **k: None)
    yield
    tenancy.set_tenant(None)


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def __iter__(self):
        return iter(self.docs)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.docs = self.docs[: int(n)]
        return self


class FakeCollection:
    """Drop-in pymongo Collection stand-in for unit tests."""

    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])
        self.find_calls: list[tuple[dict | None, dict | None]] = []
        self.count_calls: list[dict | None] = []
        self.aggregate_calls: list[list[dict]] = []

    def find(self, filter=None, projection=None):
        self.find_calls.append((filter, projection))
        return FakeCursor(self.docs)

    def find_one(self, filter=None, projection=None):
        self.find_calls.append((filter, projection))
        return self.docs[0] if self.docs else None

    def count_documents(self, filter=None):
        self.count_calls.append(filter)
        return len(self.docs)

    def estimated_document_count(self):
        return len(self.docs)

    def aggregate(self, pipeline):
        self.aggregate_calls.append(list(pipeline))
        return iter(self.docs)


@pytest.fixture
def fake_collections(monkeypatch):
    """Patch all collection accessors in `tools` to return FakeCollections.

    Yields a dict of name -> FakeCollection so tests can inspect call args.
    """
    from yoku.agent import tools

    colls = {
        "jira_tickets": FakeCollection(),
        "github_prs": FakeCollection(),
        "unified_users": FakeCollection(),
    }
    monkeypatch.setattr(tools, "tickets_collection", lambda: colls["jira_tickets"])
    monkeypatch.setattr(tools, "github_prs_collection", lambda: colls["github_prs"])
    monkeypatch.setattr(tools, "unified_users_collection", lambda: colls["unified_users"])
    monkeypatch.setattr(
        tools,
        "get_collection",
        lambda name: colls.get(name)
        or (_ for _ in ()).throw(
            ValueError(f"Unknown collection {name!r}. Allowed: {sorted(colls)}")
        ),
    )
    yield colls
