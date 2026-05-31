"""create_app refuses to boot prod-like with the built-in dev JWT secret."""

from __future__ import annotations

import pytest

from yoku.api import main
from yoku.core.config import settings


@pytest.mark.unit
def test_refuses_default_secret_in_prod(monkeypatch):
    # Test env leaves JWT_SECRET unset, so the default is in use.
    assert settings.is_default_jwt_secret
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        main.create_app()


@pytest.mark.unit
@pytest.mark.parametrize("env", ["local", "dev", "test", "ci"])
def test_allows_default_secret_in_safe_envs(monkeypatch, env):
    monkeypatch.setenv("ENV", env)
    main.create_app()  # must not raise


@pytest.mark.unit
def test_allows_prod_when_secret_overridden(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(type(settings), "is_default_jwt_secret", property(lambda _self: False))
    main.create_app()  # a real secret in prod is fine
