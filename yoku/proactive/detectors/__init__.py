"""Detector auto-discovery — mirrors `agent/tools/_registry.py`.

A new gap type is a new module in this package exposing a module-level
`Detector` instance; no central list to edit. Modules starting with `_` are
skipped.
"""

from __future__ import annotations

import importlib
import pkgutil

from yoku.proactive.detectors._base import Detector

__all__ = ["Detector", "discover_detectors"]


def discover_detectors() -> list[Detector]:
    """Collect every Detector across the submodules, sorted by name."""
    import yoku.proactive.detectors as pkg

    found: dict[str, Detector] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{pkg.__name__}.{info.name}")
        for value in vars(module).values():
            if isinstance(value, Detector):
                found[value.name] = value
    return [found[name] for name in sorted(found)]
