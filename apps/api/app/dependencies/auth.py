"""Authentication and RBAC dependencies.

`get_current_user` answers "who is this?"; `require_permission` answers
"can they do this?". Every route except `/auth/login` and `/auth/refresh`
depends on `get_current_user`; mutating routes additionally depend on
`require_permission(...)`. See `docs/security/rbac.md`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import JWTError, TokenType, decode_token
from app.db.session import get_db
from app.models.auth import User
from app.repositories.auth import UserRepository

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise AuthenticationError("Missing bearer token.")

    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    if payload.get("type") != TokenType.ACCESS.value:
        raise AuthenticationError("Access token required.")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid token subject.") from exc

    user = await UserRepository(session).get_with_permissions(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or inactive.")

    return user


def require_permission(code: str) -> Callable:
    async def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if not current_user.is_superuser and code not in current_user.permission_codes:
            raise AuthorizationError(f"Missing required permission: {code}")
        return current_user

    return _dependency
