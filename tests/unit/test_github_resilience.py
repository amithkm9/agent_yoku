"""list_pulls should skip 404/403/410 repos cleanly without killing the loop."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import requests

from yoku.pipeline.connectors.github import client as gh


def _http_error(status_code: int, message: str = "Not Found") -> requests.HTTPError:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"message": message}
    err = requests.HTTPError(response=resp)
    return err


def _failing_paginate(status_code):
    def _impl(*_args, **_kwargs):
        raise _http_error(status_code)
        yield  # pragma: no cover — makes it a generator

    return _impl


@pytest.mark.parametrize("status", [403, 404, 410])
def test_list_pulls_skips_unavailable_repos(monkeypatch, status):
    monkeypatch.setattr(gh, "_paginate", _failing_paginate(status))
    out = list(gh.list_pulls("AsatoCorp/ghost", datetime.now(UTC)))
    assert out == []


def test_list_pulls_reraises_on_other_errors(monkeypatch):
    monkeypatch.setattr(gh, "_paginate", _failing_paginate(500))
    with pytest.raises(requests.HTTPError):
        list(gh.list_pulls("AsatoCorp/server-fault", datetime.now(UTC)))
