"""Unit tests for the agent's pipeline safety validator."""

from __future__ import annotations

import pytest

from agent_yoku.agent.tools import _validate_pipeline


def test_accepts_simple_pipeline():
    _validate_pipeline([{"$match": {"status": "open"}}, {"$limit": 10}])


def test_rejects_empty_pipeline():
    with pytest.raises(ValueError, match="non-empty"):
        _validate_pipeline([])


def test_rejects_too_many_stages():
    pipeline = [{"$match": {}} for _ in range(21)]
    with pytest.raises(ValueError, match="max is 20"):
        _validate_pipeline(pipeline)


@pytest.mark.parametrize("blocked", ["$out", "$merge", "$function", "$accumulator", "$where"])
def test_blocks_write_or_codegen_stages(blocked):
    with pytest.raises(ValueError, match="blocked"):
        _validate_pipeline([{blocked: {}}])


def test_rejects_multi_key_stage():
    with pytest.raises(ValueError, match="single-key"):
        _validate_pipeline([{"$match": {}, "extra": {}}])


def test_rejects_non_operator_key():
    with pytest.raises(ValueError, match="not a Mongo operator"):
        _validate_pipeline([{"match": {}}])  # missing $
