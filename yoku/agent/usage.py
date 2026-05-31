"""Token-usage accounting for an agent turn.

A LangChain callback handler that sums prompt / completion / total tokens across
every LLM call the agent makes in one turn, then logs the totals (with session
context via the shared logger). Attach a fresh handler per turn and pass it in
`config={"callbacks": [handler]}` to `invoke` / `stream`; it works for both since
both surface token usage through `on_llm_end`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from yoku.core.logging import get_logger

log = get_logger("usage")


# Mutable accumulator (intentionally not frozen): add() sums usage across a turn.
@dataclass
class TokenUsage:
    """Accumulated token counts for one agent turn."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0

    def add(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        # Fall back to the sum when the provider omits an explicit total.
        self.total_tokens += total or (prompt + completion)
        self.llm_calls += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
        }


def _extract_counts(response: Any) -> tuple[int, int, int]:
    """Pull (prompt, completion, total) tokens from an LLMResult, provider-agnostic.

    OpenAI surfaces usage in `llm_output["token_usage"]`; newer LangChain message
    chunks carry it in `usage_metadata`. Try both, defaulting to zeros so a
    missing field never raises on the hot path.
    """
    usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    if usage:
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        total = int(usage.get("total_tokens", 0) or 0)
        return prompt, completion, total

    prompt = completion = total = 0
    for generations in getattr(response, "generations", None) or []:
        for gen in generations:
            message = getattr(gen, "message", None)
            meta = getattr(message, "usage_metadata", None) or {}
            prompt += int(meta.get("input_tokens", 0) or 0)
            completion += int(meta.get("output_tokens", 0) or 0)
            total += int(meta.get("total_tokens", 0) or 0)
    return prompt, completion, total


class UsageCallback(BaseCallbackHandler):
    """Accumulates token usage across an agent turn and logs the totals."""

    def __init__(self, *, session_id: str | None = None) -> None:
        self.session_id = session_id
        self.usage = TokenUsage()

    def on_llm_end(self, response: Any, **_kwargs: Any) -> None:
        prompt, completion, total = _extract_counts(response)
        if prompt or completion or total:
            self.usage.add(prompt, completion, total)

    def log_totals(self) -> None:
        """Emit the accumulated usage for the turn (no-op if nothing was counted)."""
        if self.usage.llm_calls == 0:
            return
        log.info(
            "token usage session=%s calls=%d prompt=%d completion=%d total=%d",
            self.session_id or "-",
            self.usage.llm_calls,
            self.usage.prompt_tokens,
            self.usage.completion_tokens,
            self.usage.total_tokens,
        )
