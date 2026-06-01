"""Integration tests for unified_users.main against a scratch mongo db.

Binds the storage accessors to the scratch db via tenancy + a unique tenant so
the real join logic (email match, name match, manual alias, github_only) runs
end-to-end without touching real tenant data.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def tenant(scratch_db):
    from yoku.db import tenancy

    tid = f"uu_{uuid.uuid4().hex[:8]}"
    tenancy.set_tenant(tid)
    yield tid
    from pymongo import MongoClient

    from yoku.config import settings
    from yoku.db.tenancy import tenant_db_name

    MongoClient(settings.mongo_uri).drop_database(tenant_db_name(tid))
    tenancy.set_tenant(None)


def _build(tenant):
    from yoku.db import unified_users as uu

    uu.main()
    from yoku.db.mongo import ds_unified_users_collection

    return list(ds_unified_users_collection().find({}, {"_id": 0}))


@pytest.mark.integration
def test_email_match_collapses_identities(tenant):
    from yoku.db.mongo import (
        dc_github_users_collection,
        dc_jira_users_collection,
    )

    dc_jira_users_collection().insert_one(
        {
            "accountId": "j1",
            "displayName": "Akshay Reddy",
            "emailAddress": "akshay@asato.ai",
            "active": True,
        }
    )
    dc_github_users_collection().insert_one(
        {"login": "internet-zero", "id": 1, "name": "Akshay", "email": "akshay@asato.ai"}
    )

    rows = _build(tenant)
    assert len(rows) == 1
    row = rows[0]
    assert row["email"] == "akshay@asato.ai"
    assert row["github"]["login"] == "internet-zero"
    assert row["match_source"] == "email"


@pytest.mark.integration
def test_github_only_user_becomes_its_own_row(tenant):
    from yoku.db.mongo import dc_github_users_collection

    dc_github_users_collection().insert_one(
        {"login": "orphan", "id": 9, "name": "Orphan", "email": "orphan@x.io"}
    )

    rows = _build(tenant)
    assert len(rows) == 1
    assert rows[0]["match_source"] == "github_only"
    assert rows[0]["jira"] is None
    assert rows[0]["github"]["login"] == "orphan"


@pytest.mark.integration
def test_manual_alias_links_known_handle(tenant):
    from yoku.db.mongo import (
        dc_github_users_collection,
        dc_jira_users_collection,
    )

    # internet-zero is in MANUAL_ALIASES -> akshay.reddy@asato.ai.
    dc_jira_users_collection().insert_one(
        {
            "accountId": "j2",
            "displayName": "Totally Different Name",
            "emailAddress": "akshay.reddy@asato.ai",
            "active": True,
        }
    )
    dc_github_users_collection().insert_one(
        {"login": "internet-zero", "id": 2, "name": "AR", "email": None}
    )

    rows = _build(tenant)
    assert len(rows) == 1
    assert rows[0]["match_source"] == "manual"
    assert rows[0]["github"]["login"] == "internet-zero"


@pytest.mark.integration
def test_bot_flag_propagates(tenant):
    from yoku.db.mongo import dc_github_users_collection

    dc_github_users_collection().insert_one(
        {"login": "dependabot", "id": 3, "name": "bot", "email": None, "is_bot": True}
    )
    rows = _build(tenant)
    assert rows[0]["is_bot"] is True


@pytest.mark.integration
def test_rebuild_is_idempotent(tenant):
    from yoku.db.mongo import dc_jira_users_collection

    dc_jira_users_collection().insert_one(
        {"accountId": "j1", "displayName": "Solo", "emailAddress": "solo@x.io", "active": True}
    )
    first = _build(tenant)
    second = _build(tenant)
    assert len(first) == len(second) == 1
    assert first[0]["user_id"] == second[0]["user_id"]
