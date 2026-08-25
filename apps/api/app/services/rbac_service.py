"""Role/Permission read + role-management writes.

Not explicitly enumerated in the endpoint list, but `users.manage` and
`roles.manage` permissions (spec) are meaningless without somewhere to
view/manage roles — `UserService` already needs `RoleRepository` to
resolve `role_ids` on user create/update, this just exposes it via API.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.rbac import Permission, Role, RolePermission
from app.repositories.rbac import PermissionRepository, RoleRepository


class RBACService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.roles = RoleRepository(session)
        self.permissions = PermissionRepository(session)

    async def list_roles(self) -> list[Role]:
        return await self.roles.list_all()

    async def list_permissions(self) -> list[Permission]:
        return await self.permissions.list_all()

    async def get_role(self, role_id: uuid.UUID) -> Role:
        role = await self.roles.get_by_id_with_permissions(role_id)
        if role is None:
            raise NotFoundError("Role not found.")
        return role

    async def create_role(
        self, *, name: str, description: str | None, permission_ids: list[uuid.UUID]
    ) -> Role:
        role = await self.roles.create(name=name, description=description)
        await self._sync_permissions(role, permission_ids)
        await self.session.commit()
        return await self.get_role(role.id)

    async def update_role(
        self,
        role_id: uuid.UUID,
        *,
        description: str | None = None,
        permission_ids: list[uuid.UUID] | None = None,
    ) -> Role:
        role = await self.get_role(role_id)
        if description is not None:
            await self.roles.update(role, description=description)
        if permission_ids is not None:
            await self._sync_permissions(role, permission_ids)
        await self.session.commit()
        return await self.get_role(role_id)

    async def _sync_permissions(self, role: Role, permission_ids: list[uuid.UUID]) -> None:
        # Query the join table directly rather than touching
        # `role.role_permissions` — under AsyncSession, accessing a
        # relationship collection that wasn't eagerly loaded in the same
        # query raises MissingGreenlet.
        existing_rows = (
            await self.session.execute(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )
        ).scalars()
        for existing in existing_rows:
            await self.session.delete(existing)
        await self.session.flush()
        for permission_id in permission_ids:
            permission = await self.permissions.get_by_id(permission_id)
            if permission is None:
                raise NotFoundError(f"Permission {permission_id} not found.")
            self.session.add(RolePermission(role_id=role.id, permission_id=permission_id))
        await self.session.flush()
