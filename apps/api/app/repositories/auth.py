from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from app.models.auth import RefreshToken, User
from app.models.rbac import Role, RolePermission, UserRole
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def _base_query(self) -> Select:
        # `BaseRepository.list` (used by `UserService.list_users` for
        # `GET /users`) runs this with no further options -- without an
        # eager-load here, `_to_response()` accessing `user.role_names`
        # (which walks `user.user_roles[].role`) triggers a lazy load on
        # an already-detached-from-IO-context AsyncSession result,
        # raising `MissingGreenlet` in production. Only `user_roles.role`
        # is loaded (not the deeper `role.role_permissions.permission`
        # chain `get_by_email`/`get_with_permissions` below also need) --
        # `role_names` never touches permissions, and listing users has
        # no reason to pull in every role's full permission set.
        return select(User).options(selectinload(User.user_roles).selectinload(UserRole.role))

    async def get_by_email(self, email: str) -> User | None:
        stmt = (
            select(User)
            .where(User.email == email)
            .options(
                selectinload(User.user_roles)
                .selectinload(UserRole.role)
                .selectinload(Role.role_permissions)
                .selectinload(RolePermission.permission)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_permissions(self, id_) -> User | None:  # noqa: ANN001
        # populate_existing: without it, a User already in this session's
        # identity map (e.g. re-fetched right after _sync_roles mutated
        # its user_roles) would keep its stale, already in-memory
        # collection instead of reflecting the just-committed change —
        # selectinload only populates unloaded collections.
        stmt = (
            select(User)
            .where(User.id == id_)
            .options(
                selectinload(User.user_roles)
                .selectinload(UserRole.role)
                .selectinload(Role.role_permissions)
                .selectinload(RolePermission.permission)
            )
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_jti(self, jti: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.jti == jti)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
