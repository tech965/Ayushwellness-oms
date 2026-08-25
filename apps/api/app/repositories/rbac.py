from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.repositories.base import BaseRepository


class RoleRepository(BaseRepository[Role]):
    model = Role

    async def get_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_permissions(self, id_) -> Role | None:  # noqa: ANN001
        # populate_existing: without it, a Role already in this session's
        # identity map (e.g. re-fetched right after _sync_permissions
        # mutated its role_permissions) would keep its stale, already
        # in-memory collection instead of reflecting the just-committed
        # change — selectinload only populates unloaded collections.
        stmt = (
            select(Role)
            .where(Role.id == id_)
            .options(selectinload(Role.role_permissions).selectinload(RolePermission.permission))
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Role]:
        stmt = select(Role).options(
            selectinload(Role.role_permissions).selectinload(RolePermission.permission)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class PermissionRepository(BaseRepository[Permission]):
    model = Permission

    async def get_by_code(self, code: str) -> Permission | None:
        stmt = select(Permission).where(Permission.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Permission]:
        result = await self.session.execute(select(Permission))
        return list(result.scalars().all())


class UserRoleRepository(BaseRepository[UserRole]):
    model = UserRole

    async def list_for_user(self, user_id) -> list[UserRole]:  # noqa: ANN001
        stmt = select(UserRole).where(UserRole.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
