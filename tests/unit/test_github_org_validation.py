from __future__ import annotations

import pytest
from pydantic import ValidationError

from yoku.schemas.api import GithubConfigIn, normalize_github_org


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AsatoCorp", "AsatoCorp"),
        ("  AsatoCorp  ", "AsatoCorp"),
        ("@AsatoCorp", "AsatoCorp"),
        ("https://github.com/AsatoCorp", "AsatoCorp"),
        ("https://github.com/AsatoCorp/", "AsatoCorp"),
        ("github.com/AsatoCorp", "AsatoCorp"),
        ("http://www.github.com/AsatoCorp", "AsatoCorp"),
        ("https://github.com/orgs/AsatoCorp", "AsatoCorp"),
        ("https://github.com/orgs/AsatoCorp/repositories", "AsatoCorp"),
        ("AsatoCorp/dc-okta-user", "AsatoCorp"),
    ],
)
def test_normalize_strips_url_noise_down_to_slug(raw: str, expected: str) -> None:
    assert normalize_github_org(raw) == expected


@pytest.mark.unit
def test_config_normalizes_pasted_url() -> None:
    cfg = GithubConfigIn(org="https://github.com/AsatoCorp", token="t")
    assert cfg.org == "AsatoCorp"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "https://github.com/",
        "-AsatoCorp",
        "AsatoCorp-",
        "Asato--Corp",
        "Asato Corp",
        "a" * 40,
    ],
)
def test_config_rejects_invalid_org(bad: str) -> None:
    with pytest.raises(ValidationError):
        GithubConfigIn(org=bad, token="t")
