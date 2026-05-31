"""Deepagent for agent_yoku: planning + a source-agnostic toolkit.

A single planning agent decomposes the question with todos and answers it over a
generalised tool surface — `semantic_search`, `get`, `linked`, and
`filter(source, …)` work across every registered connector (JIRA, GitHub, Slack
today; GitLab, Teams, Linear next), backed by a generic mongo escape hatch.
Adding a connector is a `SourceSpec` in `agent_yoku.agent.sources`, not new tools
or sub-agents.
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent

from agent_yoku.agent.prompts import load_prompt
from agent_yoku.agent.tools import get_all_tools
from agent_yoku.config import settings


def build_agent(model: str | None = None) -> Any:
    """Build and return a compiled deepagent instance.

    `model` defaults to the configured chat model (`settings.agent_model_id`);
    pass an explicit provider:model string to override it.
    """
    return create_deep_agent(
        model=model or settings.agent_model_id,
        tools=get_all_tools(),
        system_prompt=load_prompt("main"),
    )
