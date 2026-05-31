"""Password hashing + JWT round-trip unit tests for core.auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt

from yoku.core.auth import (
    _decode,
    hash_password,
    make_token,
    verify_password,
)
from yoku.core.config import settings


@pytest.mark.unit
def test_hash_then_verify_roundtrip():
    hashed = hash_password("hunter2-correct")
    assert hashed != "hunter2-correct"
    assert verify_password("hunter2-correct", hashed) is True


@pytest.mark.unit
def test_verify_rejects_wrong_password():
    hashed = hash_password("right-password")
    assert verify_password("wrong-password", hashed) is False


@pytest.mark.unit
def test_verify_handles_malformed_hash_gracefully():
    # A non-bcrypt string makes checkpw raise ValueError -> False, never crash.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


@pytest.mark.unit
def test_hash_truncates_long_passwords_to_72_bytes():
    # bcrypt caps at 72 bytes; two passwords sharing the first 72 bytes verify
    # interchangeably, proving truncation happened without crashing.
    base = "a" * 72
    hashed = hash_password(base + "EXTRA-IGNORED-TAIL")
    assert verify_password(base, hashed) is True


@pytest.mark.unit
def test_make_token_then_decode_roundtrip():
    token = make_token(user_id="u1", email="u1@x.io", tenant_id="t1", is_admin=True)
    data = _decode(token)
    assert data["sub"] == "u1"
    assert data["email"] == "u1@x.io"
    assert data["tenant_id"] == "t1"
    assert data["is_admin"] is True
    assert "exp" in data and "iat" in data


@pytest.mark.unit
def test_make_token_defaults_is_admin_false():
    data = _decode(make_token(user_id="u2", email="u2@x.io", tenant_id="t2"))
    assert data["is_admin"] is False


@pytest.mark.unit
def test_decode_rejects_garbage_token():
    with pytest.raises(HTTPException) as exc:
        _decode("this.is.not.a.jwt")
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_decode_rejects_expired_token():
    secret = settings.jwt_secret.get_secret_value()
    payload = {
        "sub": "u3",
        "email": "u3@x.io",
        "tenant_id": "t3",
        "exp": datetime.now(UTC) - timedelta(hours=1),
    }
    expired = jwt.encode(payload, secret, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _decode(expired)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_decode_rejects_wrong_signature():
    bad = jwt.encode({"sub": "x"}, "a-different-secret", algorithm="HS256")
    with pytest.raises(HTTPException):
        _decode(bad)
