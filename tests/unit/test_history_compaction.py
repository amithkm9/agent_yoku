"""Agent history compaction: cap replayed turns, keep human + final-AI each."""

from __future__ import annotations

import pytest

from yoku.db.sessions import _compact_turns


def _turn(seq: int):
    """One turn's docs: a human question + a final AI answer."""
    return [
        {"turn_seq": seq, "role": "human", "content": f"q{seq}"},
        {"turn_seq": seq, "role": "ai", "content": f"a{seq}"},
    ]


@pytest.mark.unit
def test_keeps_only_recent_turns():
    docs = [d for seq in range(1, 6) for d in _turn(seq)]  # 5 turns
    msgs = _compact_turns(docs, max_turns=2)

    # last two turns only, in order: q4, a4, q5, a5
    assert [m.content for m in msgs] == ["q4", "a4", "q5", "a5"]


@pytest.mark.unit
def test_zero_or_negative_keeps_all():
    docs = [d for seq in range(1, 4) for d in _turn(seq)]
    assert len(_compact_turns(docs, max_turns=0)) == 6  # 3 turns x 2 msgs


@pytest.mark.unit
def test_drops_intermediate_tool_call_ai_messages():
    docs = [
        {"turn_seq": 1, "role": "human", "content": "q1"},
        {"turn_seq": 1, "role": "ai", "content": "", "tool_calls": [{"id": "1", "name": "x"}]},
        {"turn_seq": 1, "role": "tool", "content": "result", "tool_call_id": "1", "name": "x"},
        {"turn_seq": 1, "role": "ai", "content": "final answer"},
    ]
    msgs = _compact_turns(docs, max_turns=5)

    # only the human question and the final tool-call-free AI message survive
    assert [m.content for m in msgs] == ["q1", "final answer"]


@pytest.mark.unit
def test_empty_is_empty():
    assert _compact_turns([], max_turns=5) == []
