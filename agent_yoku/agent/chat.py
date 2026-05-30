"""Agent-driven Q&A over Asato connector data (JIRA, GitHub, Slack, …).

Replaces the old one-shot RAG with a deepagent that plans with todos and answers
over a source-agnostic toolkit, then synthesizes a cited answer.

Usage:
    python chat.py                              # interactive
    python chat.py "what PRs touch long-term memory?"  # one-shot
"""

from __future__ import annotations

import sys
import time

from langchain_core.messages import HumanMessage

from agent_yoku.agent.agent import build_agent
from agent_yoku.log import get_logger

log = get_logger("chat")

_AGENT = None


def get_agent():
    global _AGENT
    if _AGENT is None:
        log.info("building deep agent…")
        _AGENT = build_agent()
    return _AGENT


def ask(query: str) -> dict:
    """Run the agent on a single query. Returns the final state dict."""
    t0 = time.monotonic()
    agent = get_agent()
    log.info("agent invoke query=%r", query)
    result = agent.invoke({"messages": [HumanMessage(content=query)]})
    elapsed = time.monotonic() - t0
    log.info("agent done elapsed=%.1fs messages=%d", elapsed, len(result.get("messages", [])))
    return result


def _extract_text(content: str | list | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(block["text"])
                elif "content" in block:
                    parts.append(_extract_text(block["content"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(p for p in parts if p)
    return str(content)


def final_answer(result: dict) -> str:
    """Pull the final assistant message text out of the agent state."""
    for msg in reversed(result.get("messages", [])):
        if msg.type == "ai" and not getattr(msg, "tool_calls", None):
            return _extract_text(msg.content)
    return "<no answer produced>"


def main() -> None:
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        result = ask(query)
        print(final_answer(result))
        return

    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not query:
            continue
        if query in {"exit", "quit"}:
            return
        result = ask(query)
        print(f"\n{final_answer(result)}")


if __name__ == "__main__":
    main()
