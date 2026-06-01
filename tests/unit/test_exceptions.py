"""Exception hierarchy sanity — subclasses resolve to the right bases."""

from __future__ import annotations

import pytest

from yoku.exceptions import (
    ConfigError,
    IndexEmpty,
    NotFoundError,
    PRNotFound,
    QueryAsatoError,
    RateLimited,
    TicketNotFound,
    UpstreamError,
    UserNotFound,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls",
    [
        NotFoundError,
        TicketNotFound,
        PRNotFound,
        UserNotFound,
        UpstreamError,
        RateLimited,
        ConfigError,
        IndexEmpty,
    ],
)
def test_all_errors_subclass_base(cls):
    assert issubclass(cls, QueryAsatoError)


@pytest.mark.unit
@pytest.mark.parametrize("cls", [TicketNotFound, PRNotFound, UserNotFound])
def test_not_found_subtypes(cls):
    assert issubclass(cls, NotFoundError)


@pytest.mark.unit
def test_rate_limited_is_upstream():
    assert issubclass(RateLimited, UpstreamError)


@pytest.mark.unit
def test_raise_and_catch_as_base():
    with pytest.raises(QueryAsatoError):
        raise TicketNotFound("PROJ-1")


@pytest.mark.unit
def test_error_carries_message():
    err = ConfigError("missing OPENAI_API_KEY")
    assert str(err) == "missing OPENAI_API_KEY"
