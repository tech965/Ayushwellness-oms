"""Password hashing and JWT token utilities.

Passwords are hashed with Argon2 (via passlib). JWTs are signed with
HS256 using JWT_SECRET. Access tokens are short-lived; refresh tokens are
long-lived and persisted server-side (see models.auth.RefreshToken) so
they can be revoked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    return _create_token(
        subject=subject,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(seconds=settings.JWT_ACCESS_TOKEN_EXPIRE),
        extra_claims=extra_claims,
    )


def create_refresh_token(subject: str) -> tuple[str, str]:
    """Returns (token, jti). The jti is persisted so the token can be revoked."""
    jti = str(uuid.uuid4())
    token = _create_token(
        subject=subject,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(seconds=settings.JWT_REFRESH_TOKEN_EXPIRE),
        extra_claims={"jti": jti},
    )
    return token, jti


def _create_token(
    *,
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Raises jose.JWTError on invalid/expired tokens."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


__all__ = [
    "JWTError",
    "TokenType",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
