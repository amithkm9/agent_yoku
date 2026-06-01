"""Per-tenant connector configuration store.

Each tenant brings its own JIRA / GitHub credentials. Configs live in a
`connector_configs` collection inside the tenant's own db (so isolation
piggybacks on the existing per-tenant db routing in `storage.mongo`).

Secret fields (API tokens) are encrypted at rest with Fernet. The key is
derived from `settings.connector_secret_key` if set, otherwise from
`settings.jwt_secret` — that's fine for dev; production should set the
dedicated key so rotating JWTs doesn't invalidate stored connector creds.

The collection is intentionally separate from `auth_users` / chat collections
so dropping it doesn't lose user accounts.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pymongo.collection import Collection

from yoku.config import settings
from yoku.constants import SUPPORTED_CONNECTORS
from yoku.db.tenancy import tenant_db_name

_COLL_NAME = "connector_configs"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Derive a Fernet key from the configured secret.

    Cached so we don't rehash on every call. If you rotate the secret, restart
    the process (or call `_fernet.cache_clear()` from a test).
    """
    raw = (
        settings.connector_secret_key.get_secret_value()
        if settings.connector_secret_key
        else settings.jwt_secret.get_secret_value()
    )
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secrets(payload: dict[str, Any]) -> bytes:
    """Encrypt a JSON-serializable dict; returns ciphertext bytes."""
    return _fernet().encrypt(json.dumps(payload).encode("utf-8"))


def decrypt_secrets(ciphertext: bytes) -> dict[str, Any]:
    """Inverse of `encrypt_secrets`. Returns {} on tamper / wrong-key."""
    from yoku.logging import get_logger as _get_logger

    try:
        plain = _fernet().decrypt(ciphertext)
    except InvalidToken:
        _get_logger("connector_configs").warning(
            "failed to decrypt connector secrets — possible key rotation or data corruption"
        )
        return {}
    return json.loads(plain.decode("utf-8"))


def _coll() -> Collection:
    from yoku.db.mongo import _client  # local import to avoid cycle

    coll = _client()[tenant_db_name()][_COLL_NAME]
    coll.create_index("name", unique=True)
    return coll


def get_config(name: str) -> dict | None:
    """Return the stored config doc for `name` (or None). Includes ciphertext."""
    return _coll().find_one({"name": name})


def get_config_decrypted(name: str) -> dict | None:
    """Return {**config, **secrets} for the named connector, decrypted.

    The result merges non-secret config + decrypted secret payload into a flat
    dict (e.g. `{base_url, email, token, project}` for jira). Returns None if
    the connector is not configured for the current tenant.
    """
    doc = get_config(name)
    if not doc:
        return None
    secrets = decrypt_secrets(doc.get("secrets_enc", b"")) if doc.get("secrets_enc") else {}
    return {**(doc.get("config") or {}), **secrets}


def upsert_config(
    *,
    name: str,
    config: dict[str, Any],
    secrets: dict[str, Any],
    user_id: str,
) -> dict:
    """Insert or update a connector config.

    `config` holds non-secret fields (base_url, project, org…). `secrets` holds
    fields that get encrypted (token). Returns the stored doc (without
    ciphertext).
    """
    if name not in SUPPORTED_CONNECTORS:
        raise ValueError(f"unsupported connector: {name}")

    now = datetime.now(UTC)
    update = {
        "name": name,
        "config": config,
        "secrets_enc": encrypt_secrets(secrets),
        "updated_at": now,
        "updated_by": user_id,
    }
    # Don't reset sync metadata on re-save — that's owned by the sync flow.
    _coll().update_one(
        {"name": name}, {"$set": update, "$setOnInsert": {"created_at": now}}, upsert=True
    )
    return get_config(name) or update


def delete_config(name: str) -> bool:
    return _coll().delete_one({"name": name}).deleted_count > 0


def list_configs() -> list[dict]:
    """List all configured connectors for the current tenant (no ciphertext)."""
    return list(_coll().find({}, {"secrets_enc": 0, "_id": 0}))


def mark_synced(name: str, *, ok: bool, error: str | None = None) -> None:
    """Record the outcome of a sync run on the config doc."""
    _coll().update_one(
        {"name": name},
        {
            "$set": {
                "last_synced_at": datetime.now(UTC) if ok else None,
                "last_sync_status": "ok" if ok else "error",
                "last_sync_error": None if ok else (error or "unknown error"),
            }
        },
    )
