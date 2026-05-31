"""Unit tests for the per-tenant connector_configs store.

Focuses on the parts that don't need a live mongo: encryption round-trip,
tamper detection, and tenant-id validation. The CRUD path is covered by
integration tests that hit a scratch mongo.
"""

from __future__ import annotations

import pytest

from yoku.core.storage import connector_configs as cc
from yoku.core.storage import tenancy


class TestEncryption:
    def test_round_trip(self):
        payload = {"token": "secret-123", "extra": "value"}
        blob = cc.encrypt_secrets(payload)
        assert blob != payload  # encrypted, not raw
        assert isinstance(blob, bytes)
        assert cc.decrypt_secrets(blob) == payload

    def test_tampered_ciphertext_returns_empty(self):
        blob = cc.encrypt_secrets({"token": "x"})
        tampered = blob[:-1] + bytes([(blob[-1] ^ 1) & 0xFF])
        assert cc.decrypt_secrets(tampered) == {}

    def test_distinct_inputs_produce_distinct_ciphertexts(self):
        a = cc.encrypt_secrets({"token": "alice"})
        b = cc.encrypt_secrets({"token": "bob"})
        assert a != b


class TestTenantIdValidation:
    @pytest.mark.parametrize(
        "tid",
        ["asato", "acme-corp", "tenant_42", "a1", "0test", "x" * 40],
    )
    def test_valid_ids(self, tid):
        assert tenancy.is_valid_tenant_id(tid) is True

    @pytest.mark.parametrize(
        "tid",
        [
            "",
            "a",  # too short
            "x" * 41,  # too long
            "ACME",  # uppercase
            "has space",
            "has.dot",
            "_starts-with-underscore",  # must start alphanumeric
            "-starts-with-dash",
            "tenant!",
            "tenant/slash",
        ],
    )
    def test_invalid_ids(self, tid):
        assert tenancy.is_valid_tenant_id(tid) is False

    def test_normalize_lowercases_and_strips(self):
        assert tenancy.normalize_tenant_id("  ACME-Corp  ") == "acme-corp"
