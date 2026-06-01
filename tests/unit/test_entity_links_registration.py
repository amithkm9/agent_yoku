"""entity_links is registered so the generic agent tools can read it."""

from __future__ import annotations

import pytest

from yoku.agent import schema_registry as sr
from yoku.core.storage import mongo


@pytest.mark.unit
def test_entity_links_is_an_allowed_collection():
    assert "entity_links" in mongo.ALLOWED_COLLECTIONS


@pytest.mark.unit
def test_entity_links_has_a_model_and_description():
    assert sr.model_for("entity_links") is not None
    desc = sr.collection_description("entity_links")
    assert "cross-source" in desc.lower()


@pytest.mark.unit
def test_entity_links_filterable_fields():
    args = {f.arg for f in sr.filter_fields("entity_links")}
    assert {"from_key", "to_key", "link_type", "extractor"} <= args


@pytest.mark.unit
def test_entity_links_display_fields():
    fields = set(sr.display_fields("entity_links"))
    assert {"from_key", "to_key", "link_type"} <= fields
