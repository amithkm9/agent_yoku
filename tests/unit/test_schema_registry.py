"""Schema registry derives collection details from the Pydantic models."""

from __future__ import annotations

import pytest

from yoku.agent import schema_registry as sr


@pytest.mark.unit
def test_description_comes_from_model_docstring():
    assert sr.collection_description("jira_tickets").startswith("Asato JIRA tickets")
    # slack previously had no description; now sourced from the model.
    assert sr.collection_description("slack_messages")
    assert sr.collection_description("unknown_collection") == ""


@pytest.mark.unit
def test_display_fields_drive_projection():
    proj = sr.projection("jira_tickets")
    assert proj["key"] == 1 and proj["status"] == 1
    assert "description" not in proj  # not a display field


@pytest.mark.unit
def test_filter_fields_carry_kind_identity_and_normalize():
    by_arg = {f.arg: f for f in sr.filter_fields("jira_tickets")}
    assert by_arg["assignee"].kind == sr.USER
    assert by_arg["assignee"].identity == "jira.displayName"
    assert by_arg["label"].mongo_field == "labels"  # arg != field

    gh = {f.arg: f for f in sr.filter_fields("github_prs")}
    assert gh["has_jira_link"].kind == sr.HAS_REFS
    assert gh["repo"].normalize is not None
    assert gh["repo"].normalize("agent-svc") == "AsatoCorp/agent-svc"


@pytest.mark.unit
def test_field_specs_report_type_and_flags():
    specs = {s.name: s for s in sr.field_specs("github_prs")}
    assert specs["jira_keys"].type == "array[string]"
    assert specs["status"].filterable is True
    assert specs["description"].display is False


@pytest.mark.unit
def test_field_specs_carry_filter_semantics_and_enum():
    specs = {s.name: s for s in sr.field_specs("github_prs")}
    # person field exposes its arg, kind, and the identity it resolves to
    assert specs["author"].filter_arg == "author"
    assert specs["author"].filter_kind == sr.USER
    assert specs["author"].identity == "github.login"
    # closed-set field exposes its enum
    assert specs["status"].enum is not None and "merged" in specs["status"].enum
    # non-filterable fields carry no filter metadata
    assert specs["description"].filter_arg is None
    assert specs["description"].filter_kind is None


@pytest.mark.unit
@pytest.mark.parametrize("collection", ["jira_tickets", "github_prs", "slack_messages"])
def test_every_source_projection_includes_key(collection):
    # key must be a display field or cards/links come back without an identifier.
    assert "key" in sr.display_fields(collection)


@pytest.mark.unit
def test_unknown_collection_yields_nothing():
    assert sr.filter_fields("nope") == []
    assert sr.projection("nope") == {}
    assert sr.field_specs("nope") == []
