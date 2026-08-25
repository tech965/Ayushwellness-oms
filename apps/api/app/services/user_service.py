from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.models.auth import User
from app.models.rbac import UserRole
from app.repositories.auth import UserRepository
from app.repositories.rbac import RoleRepository
from app.schemas.common import PageParams, SortParams


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)

    async def list_users(
        self, *, page_params: PageParams, sort_params: SortParams
    ) -> tuple[list[User], int]:
        items, total = await self.users.list(page_params=page_params, sort_params=sort_params)
        return list(items), total

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.users.get_with_permissions(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def create_user(
        self, *, name: str, email: str, phone: str | None, password: str, role_ids: list[uuid.UUID]
    ) -> User:
        if await self.users.get_by_email(email) is not None:
            raise ConflictError("A user with this email already exists.")

        user = await self.users.create(
            name=name, email=email, phone=phone, password_hash=hash_password(password)
        )
        await self._sync_roles(user, role_ids)
        await self.session.commit()
        return await self.get_user(user.id)

    async def update_user(
        self,
        user_id: uuid.UUID,
        *,
        name: str | None = None,
        phone: str | None = None,
        is_active: bool | None = None,
        role_ids: list[uuid.UUID] | None = None,
    ) -> User:
        user = await self.get_user(user_id)
        fields = {
            k: v
            for k, v in {"name": name, "phone": phone, "is_active": is_active}.items()
            if v is not None
        }
        if fields:
            await self.users.update(user, **fields)
        if role_ids is not None:
            await self._sync_roles(user, role_ids)
        await self.session.commit()
        return await self.get_user(user_id)

    async def deactivate_user(self, user_id: uuid.UUID) -> User:
        return await self.update_user(user_id, is_active=False)

    async def _sync_roles(self, user: User, role_ids: list[uuid.UUID]) -> None:
        # Query the join table directly rather than touching
        # `user.user_roles` — under AsyncSession, accessing a relationship
        # collection that wasn't eagerly loaded in the same query raises
        # MissingGreenlet.
        existing_rows = (
            await self.session.execute(select(UserRole).where(UserRole.user_id == user.id))
        ).scalars()
        for existing in existing_rows:
            await self.session.delete(existing)
        await self.session.flush()
        for role_id in role_ids:
            role = await self.roles.get_by_id(role_id)
            if role is None:
                raise NotFoundError(f"Role {role_id} not found.")
            self.session.add(UserRole(user_id=user.id, role_id=role_id))
        await self.session.flush()
