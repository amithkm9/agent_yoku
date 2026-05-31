"""FastAPI dependencies: auth, current user, tenant context.

Auth uses HS256 JWTs signed with settings.jwt_secret. Tokens carry sub (user
id), email, tenant_id, and exp. The current_user dependency binds the
session's tenant_id into the request-scoped ContextVar so storage helpers
route to the right mongo db.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from yoku.api.schemas import UserOut
from yoku.core.config import settings
from yoku.core.logging import set_session
from yoku.core.storage import tenancy

# tokenUrl is relative to /api root; the React client will call /api/auth/login.
_oauth2 = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=True)

# bcrypt caps inputs at 72 bytes; truncate proactively (industry-standard
# workaround) so longer passwords don't crash hashpw on bcrypt >=4.1.
_BCRYPT_MAX = 72


def hash_password(plain: str) -> str:
    raw = plain.encode("utf-8")[:_BCRYPT_MAX]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    raw = plain.encode("utf-8")[:_BCRYPT_MAX]
    try:
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except ValueError:
        return False


def make_token(*, user_id: str, email: str, tenant_id: str, is_admin: bool = False) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "tenant_id": tenant_id,
        "is_admin": is_admin,
        "exp": now + timedelta(hours=settings.jwt_ttl_hours),
        "iat": now,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"])
    except JWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e


async def current_user(token: Annotated[str, Depends(_oauth2)]) -> UserOut:
    """Validate the JWT, bind tenant context, return UserOut.

    Side effect: sets `tenancy.current_tenant_var` and `log.session_id_var` for
    the duration of the request so every storage call lands in the right db.
    """
    data = _decode(token)
    user = UserOut(
        id=data["sub"],
        email=data["email"],
        tenant_id=data["tenant_id"],
        is_admin=data.get("is_admin", False),
    )
    tenancy.set_tenant(user.tenant_id)
    set_session(user.id)
    return user


CurrentUser = Annotated[UserOut, Depends(current_user)]


async def require_admin(user: CurrentUser) -> UserOut:
    """Gate endpoints behind tenant-admin. 403s ordinary users."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin privilege required")
    return user


AdminUser = Annotated[UserOut, Depends(require_admin)]
