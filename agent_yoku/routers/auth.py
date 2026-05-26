"""Login / register endpoints + bcrypt user store.

Per-tenant: an `auth_users` collection lives in each tenant's db. Sign-up is
gated on a `signup_tenant` query param so users explicitly choose which tenant
db they create their account in (the same mongo cluster, different db).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm

from agent_yoku.deps import CurrentUser, hash_password, make_token, verify_password
from agent_yoku.schemas import LoginRequest, TokenResponse, UserOut
from agent_yoku.storage import tenancy
from agent_yoku.storage.mongo import auth_users_collection

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- Internal helpers ----------


def _to_user_out(doc: dict) -> UserOut:
    return UserOut(
        id=doc["user_id"],
        email=doc["email"],
        name=doc.get("name"),
        tenant_id=doc["tenant_id"],
        is_admin=doc.get("is_admin", False),
    )


# ---------- OAuth2-compatible token endpoint (for FastAPI Swagger + clients) ----------


@router.post("/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    tenant: str = Query(
        default=None, description="Tenant id; defaults to settings.default_tenant_id"
    ),
) -> TokenResponse:
    """Trade email+password for a JWT.

    Uses the OAuth2PasswordRequestForm so it's compatible with the FastAPI
    Swagger 'Authorize' button. JSON variant lives at /auth/login-json.
    """
    if tenant:
        tenancy.set_tenant(tenant)
    doc = auth_users_collection().find_one({"email": form.username.lower()})
    if not doc or not verify_password(form.password, doc["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    user = _to_user_out(doc)
    token = make_token(
        user_id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        is_admin=user.is_admin,
    )
    return TokenResponse(access_token=token, user=user)


@router.post("/login-json", response_model=TokenResponse)
async def login_json(req: LoginRequest, tenant: str = Query(default=None)) -> TokenResponse:
    """JSON-bodied variant — the React UI uses this."""
    if tenant:
        tenancy.set_tenant(tenant)
    doc = auth_users_collection().find_one({"email": req.email.lower()})
    if not doc or not verify_password(req.password, doc["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    user = _to_user_out(doc)
    token = make_token(
        user_id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        is_admin=user.is_admin,
    )
    return TokenResponse(access_token=token, user=user)


# ---------- Sign-up (open by default; gate at deploy time if you want closed) ----------


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    req: LoginRequest,
    tenant: str = Query(default=None, description="Tenant id to create the account in"),
    name: str | None = Query(default=None),
) -> TokenResponse:
    """Create an account in the specified tenant's db.

    The first user of a tenant becomes its admin automatically. Tenant ids
    are case-insensitive and lowercased.
    """
    if tenant:
        tenancy.set_tenant(tenant.lower())

    coll = auth_users_collection()
    if coll.find_one({"email": req.email.lower()}):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered in this tenant")

    is_first = coll.count_documents({}) == 0
    doc = {
        "user_id": str(uuid.uuid4()),
        "email": req.email.lower(),
        "name": name,
        "password_hash": hash_password(req.password),
        "tenant_id": tenancy.current_tenant(),
        "is_admin": is_first,
        "created_at": datetime.now(UTC),
    }
    coll.insert_one(doc)

    user = _to_user_out(doc)
    token = make_token(
        user_id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        is_admin=user.is_admin,
    )
    return TokenResponse(access_token=token, user=user)


# ---------- "Who am I" ----------


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return user
