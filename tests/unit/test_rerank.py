"""ZeroEntropy reranker wrapper — hermetic: the client is stubbed, no network."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from agent_yoku.agent import rerank as rr


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(rr.settings, "rerank_enabled", True)
    monkeypatch.setattr(rr.settings, "zeroentropy_api_key", SecretStr("test-key"))


class _Result:
    def __init__(self, index: int, relevance_score: float):
        self.index = index
        self.relevance_score = relevance_score


class _Response:
    def __init__(self, results):
        self.results = results
        self.total_tokens = 42


def _stub_client(monkeypatch, *, rerank_impl) -> None:
    models = type("Models", (), {"rerank": staticmethod(rerank_impl)})()
    client = type("Client", (), {"models": models})()
    monkeypatch.setattr(rr, "_client", lambda: client)


@pytest.mark.unit
def test_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(rr.settings, "rerank_enabled", False)
    assert rr.rerank("q", ["a", "b"]) is None


@pytest.mark.unit
def test_returns_none_when_no_documents(monkeypatch):
    _enable(monkeypatch)
    assert rr.rerank("q", []) is None


@pytest.mark.unit
def test_returns_index_score_pairs_best_first(monkeypatch):
    _enable(monkeypatch)
    _stub_client(
        monkeypatch,
        rerank_impl=lambda **_kw: _Response([_Result(2, 0.9), _Result(0, 0.4)]),
    )
    assert rr.rerank("q", ["a", "b", "c"]) == [(2, 0.9), (0, 0.4)]


@pytest.mark.unit
def test_returns_none_on_api_error(monkeypatch):
    _enable(monkeypatch)

    def _boom(**_kw):
        raise RuntimeError("zeroentropy down")

    _stub_client(monkeypatch, rerank_impl=_boom)
    assert rr.rerank("q", ["a", "b"]) is None


@pytest.mark.unit
def test_passes_latency_fast_to_client(monkeypatch):
    _enable(monkeypatch)
    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return _Response([_Result(0, 0.9)])

    _stub_client(monkeypatch, rerank_impl=_capture)
    rr.rerank("q", ["a"])
    assert captured.get("latency") == "fast"
