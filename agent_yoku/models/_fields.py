"""Helper for declaring document fields with agent-facing metadata.

A collection's Pydantic model is the single source of truth for the agent: each
field carries its `description` plus flags the agent layer reads via the schema
registry —

- `display`    — include in lean result cards (drives the search/filter projection)
- `filter_arg` — expose as a `filter(source, …)` criterion under this name
- `filter_kind`— "exact" | "user" | "has_refs"
- `identity`   — for `user` fields: dotted path in `unified_users` to resolve aliases
- `normalize`  — named value canonicaliser (e.g. "gh_repo")

Keeping these on the field (not in a separate hardcoded table) means onboarding a
collection or changing what's filterable is a schema edit, nothing else.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field


def doc_field(
    description: str,
    *,
    default: Any = None,
    default_factory: Any = None,
    display: bool = False,
    filter_arg: str | None = None,
    filter_kind: str = "exact",
    identity: str | None = None,
    normalize: str | None = None,
) -> Any:
    """Build a Pydantic `Field` carrying agent metadata in `json_schema_extra`."""
    extra: dict[str, Any] = {}
    if display:
        extra["display"] = True
    if filter_arg:
        extra["filterable"] = True
        extra["filter_arg"] = filter_arg
        extra["filter_kind"] = filter_kind
        if identity:
            extra["identity"] = identity
        if normalize:
            extra["normalize"] = normalize

    kwargs: dict[str, Any] = {"description": description}
    if extra:
        kwargs["json_schema_extra"] = extra
    if default_factory is not None:
        return Field(default_factory=default_factory, **kwargs)
    return Field(default, **kwargs)
