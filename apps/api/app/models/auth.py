"""User accounts and persisted refresh tokens.

`RefreshToken` exists so a refresh token can be revoked server-side
(logout, admin-forced revocation) — it isn't valid forever just because
it hasn't expired. See `app/core/security.py` for token issuance/decoding
and `docs/api/authentication.md` for the full design.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.rbac import UserRole


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Telecalling hierarchy: a Telecaller belongs to exactly one Team
    # Leader. Self-referential rather than a separate `Team` table — "the
    # team" *is* "the set of users where team_leader_id == this user's
    # id." NULL for every user who isn't a Telecaller (including Team
    # Leaders themselves, who aren't managed by another team leader).
    team_leader_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    user_roles: Mapped[list[UserRole]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    team_leader: Mapped[User | None] = relationship(remote_side="User.id")

    @property
    def permission_codes(self) -> set[str]:
        """Union of every permission granted by every role this user holds."""
        codes: set[str] = set()
        for user_role in self.user_roles:
            for role_permission in user_role.role.role_permissions:
                codes.add(role_permission.permission.code)
        return codes

    @property
    def role_names(self) -> list[str]:
        return [user_role.role.name for user_role in self.user_roles]


class RefreshToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    user: Mapped[User] = relationship()

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)
