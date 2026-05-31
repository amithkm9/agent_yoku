"""Export Pydantic models as JSON Schema files under ./schemas.

Run via `make schemas`. Useful for documentation, sharing with downstream
consumers, or feeding the schema to an LLM that needs to compose docs of
the right shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the package importable when run as a plain script (without installing).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yoku.core.models import (
    ChatMessage,
    ChatSession,
    GitHubPR,
    JiraTicket,
    UnifiedUser,
)

_OUT = Path(__file__).resolve().parent.parent / "schemas"
_MODELS = [JiraTicket, GitHubPR, UnifiedUser, ChatSession, ChatMessage]


def main() -> None:
    _OUT.mkdir(exist_ok=True)
    for model in _MODELS:
        path = _OUT / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2))
        print(f"  wrote {path.relative_to(_OUT.parent)}")


if __name__ == "__main__":
    main()
