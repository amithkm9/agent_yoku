"""Tool auto-discovery.

Mirrors the connector discovery pattern (`connectors.base.list_connectors`):
walk this package's modules and collect every module-level LangChain tool. A new
tool file is picked up automatically — no central list to edit. Modules whose
name starts with `_` (shared helpers, this registry) are skipped.
"""

from __future__ import annotations

import importlib
import pkgutil

from langchain_core.tools import BaseTool


def discover_tools() -> list[BaseTool]:
    """Collect every LangChain tool defined across the tool submodules.

    Returns them sorted by tool name so the toolkit order is stable regardless of
    module import order.
    """
    import yoku.agent.tools as pkg

    found: dict[str, BaseTool] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{pkg.__name__}.{info.name}")
        for value in vars(module).values():
            if isinstance(value, BaseTool):
                found[value.name] = value
    return [found[name] for name in sorted(found)]
