"""Versioned prompt assets, loaded from this package's `.md` files.

Keeping the system prompt as a checked-in asset (rather than an inline string)
makes prompt changes show up as readable diffs and lets the prompt be versioned
independently of the agent wiring. `load_prompt("main")` reads `main.md`.
"""

from __future__ import annotations

from importlib import resources

_SUFFIX = ".md"


def load_prompt(name: str) -> str:
    """Return the text of the named prompt asset (e.g. "main" → main.md)."""
    resource = resources.files(__package__).joinpath(f"{name}{_SUFFIX}")
    return resource.read_text(encoding="utf-8")
