"""Relationship registry — loads cross-collection links from relationships.yaml.

Mirrors asato-common's relationships model: a declarative list of
`(entity1, entity2, join)` records. `linked` traverses them and
`list_collections` surfaces them, so cross-collection wiring is data, never
hardcoded in the tool layer. Add a connector's links by editing the YAML.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

_YAML_PATH = Path(__file__).with_name("relationships.yaml")


class Join(BaseModel):
    """How two entities join: entity1.local_field references entity2.foreign_field."""

    local_field: str
    foreign_field: str


class Relationship(BaseModel):
    """A directed link from entity1 (the referrer) to entity2 (the referenced)."""

    name: str
    entity1: str  # collection name holding the reference
    entity2: str  # collection name referenced
    relationship_type: str = "many-to-many"
    join: Join
    description: str = ""


class RelationshipRegistry(BaseModel):
    relationships: list[Relationship]


@lru_cache(maxsize=1)
def _registry() -> RelationshipRegistry:
    data = yaml.safe_load(_YAML_PATH.read_text()) or {}
    return RelationshipRegistry.model_validate(data)


def all_relationships() -> list[Relationship]:
    return list(_registry().relationships)


def outbound_relationships(collection: str) -> list[Relationship]:
    """Relationships where `collection` is the referrer (entity1)."""
    return [r for r in _registry().relationships if r.entity1 == collection]


def inbound_relationships(collection: str) -> list[Relationship]:
    """Relationships where `collection` is the referenced entity (entity2)."""
    return [r for r in _registry().relationships if r.entity2 == collection]


def relationships_for(collection: str) -> list[Relationship]:
    """Every relationship `collection` participates in, either side."""
    return [r for r in _registry().relationships if collection in (r.entity1, r.entity2)]
