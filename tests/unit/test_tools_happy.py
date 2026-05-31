"""Happy-path + edge coverage for resolve_user via the registry.

These exercise the real tool functions over the patched _resolve_user_doc seam.
"""

from __future__ import annotations

import pytest

from yoku.agent import tools


@pytest.mark.unit
def test_resolve_user_found(monkeypatch):
    doc = {"email": "x@y.io", "jira": {"displayName": "X"}, "match_source": "name"}
    monkeypatch.setattr(tools, "_resolve_user_doc", lambda _q: doc)
    out = tools.resolve_user.invoke({"query": "X"})
    assert out["email"] == "x@y.io"


@pytest.mark.unit
def test_resolve_user_not_found_raises(monkeypatch):
    monkeypatch.setattr(tools, "_resolve_user_doc", lambda _q: None)
    with pytest.raises(ValueError):
        tools.resolve_user.invoke({"query": "nobody"})
