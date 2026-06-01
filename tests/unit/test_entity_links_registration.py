"""entity_links is registered so the generic agent tools can read it."""

from __future__ import annotations

import pytest

from yoku.agent import schema_registry as sr
from yoku.core.storage import mongo


@pytest.mark.unit
def test_entity_links_is_an_allowed_collection():
    assert "ds-entity-links" in mongo.ALLOWED_COLLECTIONS


@pytest.mark.unit
def test_entity_links_has_a_model_and_description():
    assert sr.model_for("ds-entity-links") is not None
    desc = sr.collection_description("ds-entity-links")
    assert "cross-source" in desc.lower()


@pytest.mark.unit
def test_entity_links_filterable_fields():
    args = {f.arg for f in sr.filter_fields("ds-entity-links")}
    assert {"from_key", "to_key", "link_type", "extractor"} <= args


@pytest.mark.unit
def test_entity_links_display_fields():
    fields = set(sr.display_fields("ds-entity-links"))
    assert {"from_key", "to_key", "link_type"} <= fields
