"""BSON -> JSON-safe value conversion.

Mongo returns ObjectId and datetime objects that don't serialize to JSON
out of the box. `bson_safe` walks any structure and replaces them with
strings (ObjectId) / ISO-format strings (datetime).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId


def bson_safe(obj: Any) -> Any:
    """Recursively convert ObjectId/datetime to JSON-safe primitives."""
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: bson_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [bson_safe(v) for v in obj]
    return obj
