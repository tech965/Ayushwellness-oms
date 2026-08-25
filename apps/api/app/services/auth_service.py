"""Login / refresh / logout business logic.

Refresh tokens are persisted (`RefreshToken`, keyed by `jti`) so they can
be revoked server-side on logout — a stolen refresh token isn't valid
forever just because it hasn't expired. See `docs/api/authentication.md`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.core.security import (
    JWTError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.repositories.auth import RefreshTokenRepository, UserRepository


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def login(self, *, email: str, password: str) -> tuple[str, str]:
        user = await self.users.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid email or password.")
        if not user.is_active:
            raise AuthenticationError("Account is deactivated.")

        access_token = create_access_token(subject=str(user.id))
        refresh_token, jti = create_refresh_token(subject=str(user.id))
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.JWT_REFRESH_TOKEN_EXPIRE)
        await self.refresh_tokens.create(jti=jti, user_id=user.id, expires_at=expires_at)
        await self.session.commit()
        return access_token, refresh_token

    async def refresh(self, *, refresh_token: str) -> str:
        try:
            payload = decode_token(refresh_token)
        except JWTError as exc:
            raise AuthenticationError("Invalid or expired refresh token.") from exc

        if payload.get("type") != TokenType.REFRESH.value:
            raise AuthenticationError("Refresh token required.")

        jti = payload.get("jti")
        stored = await self.refresh_tokens.get_by_jti(jti) if jti else None
        if stored is None or not stored.is_active:
            raise AuthenticationError("Refresh token has been revoked or expired.")

        user = await self.users.get_by_id(uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise AuthenticationError("User not found or inactive.")

        return create_access_token(subject=str(user.id))

    async def logout(self, *, refresh_token: str) -> None:
        try:
            payload = decode_token(refresh_token)
        except JWTError:
            return  # already unusable — logout is idempotent
        jti = payload.get("jti")
        if not jti:
            return
        stored = await self.refresh_tokens.get_by_jti(jti)
        if stored is not None and stored.revoked_at is None:
            await self.refresh_tokens.update(stored, revoked_at=datetime.now(UTC))
            await self.session.commit()
