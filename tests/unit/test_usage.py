"""UsageCallback accumulates token counts across an agent turn."""

from __future__ import annotations

import pytest

from agent_yoku.agent.usage import UsageCallback


class _OpenAIResult:
    """Mimics an OpenAI LLMResult: usage under llm_output['token_usage']."""

    def __init__(self, prompt, completion, total):
        self.llm_output = {
            "token_usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
            }
        }


class _Gen:
    def __init__(self, meta):
        self.message = type("M", (), {"usage_metadata": meta})()


class _MetaResult:
    """Mimics a result that only exposes usage_metadata on message chunks."""

    llm_output = None

    def __init__(self, generations):
        self.generations = generations


@pytest.mark.unit
def test_accumulates_across_calls():
    cb = UsageCallback(session_id="s1")
    cb.on_llm_end(_OpenAIResult(10, 5, 15))
    cb.on_llm_end(_OpenAIResult(20, 8, 28))

    assert cb.usage.prompt_tokens == 30
    assert cb.usage.completion_tokens == 13
    assert cb.usage.total_tokens == 43
    assert cb.usage.llm_calls == 2


@pytest.mark.unit
def test_reads_usage_metadata_fallback():
    cb = UsageCallback()
    result = _MetaResult([[_Gen({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})]])
    cb.on_llm_end(result)

    assert cb.usage.prompt_tokens == 7
    assert cb.usage.completion_tokens == 3
    assert cb.usage.total_tokens == 10
    assert cb.usage.llm_calls == 1


@pytest.mark.unit
def test_total_falls_back_to_sum_when_missing():
    cb = UsageCallback()
    cb.on_llm_end(_OpenAIResult(10, 5, 0))
    assert cb.usage.total_tokens == 15


@pytest.mark.unit
def test_empty_response_is_ignored():
    cb = UsageCallback()
    cb.on_llm_end(_OpenAIResult(0, 0, 0))
    assert cb.usage.llm_calls == 0
    assert cb.usage.as_dict() == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
    }


def _capture(monkeypatch) -> list[str]:
    """Record messages emitted by the usage logger.

    The agent_yoku logger sets propagate=False, so pytest's caplog (which hangs
    off the root logger) never sees these records — capture them directly.
    """
    from agent_yoku.agent import usage as usage_mod

    messages: list[str] = []
    monkeypatch.setattr(usage_mod.log, "info", lambda msg, *a: messages.append(msg % a))
    return messages


@pytest.mark.unit
def test_log_totals_emits_when_counted(monkeypatch):
    messages = _capture(monkeypatch)
    cb = UsageCallback(session_id="sess-9")
    cb.on_llm_end(_OpenAIResult(12, 4, 16))
    cb.log_totals()
    assert any("token usage" in m for m in messages)
    assert any("session=sess-9" in m for m in messages)


@pytest.mark.unit
def test_log_totals_noop_when_nothing_counted(monkeypatch):
    messages = _capture(monkeypatch)
    cb = UsageCallback()
    cb.log_totals()
    assert not any("token usage" in m for m in messages)
