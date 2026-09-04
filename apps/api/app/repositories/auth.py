from __future__ import annotations

import uuid

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

    async def list_by_role(
        self,
        role_name: str,
        *,
        team_leader_id: uuid.UUID | None = None,
        active_only: bool = True,
    ) -> list[User]:
        """Every user holding `role_name` — the roster source for
        "assignable Telecaller" dropdowns (`TelecallingService.
        list_assignable_telecallers`). Deliberately independent of
        `OrderAssignmentRepository`/`CheckoutAssignmentRepository`'s
        `telecaller_performance` (which only ever returns telecallers who
        already have at least one active assignment) — a brand-new
        Telecaller with zero assignments so far must still be selectable.
        `team_leader_id=None` returns every holder of the role across
        every team (the Admin case); a caller scoping to one Team Leader's
        own roster passes their own id, matching every other `/team/*`
        scoping convention in this codebase.

        Matches `Role.name` case-insensitively: `Role.name` (`RoleCreateRequest.
        name`, app/schemas/rbac.py) is free-typed by an Admin via
        Administration -> Roles with no normalization anywhere in
        `RBACService.create_role`, and `TELECALLER`/`TEAM_LEADER` aren't
        part of the default seeded roles (scripts/seed.py) -- they must be
        created by hand. A role typed as e.g. "Telecaller" previously
        matched nothing here (exact `==`), silently emptying this roster
        while the role itself worked everywhere else (Users page, RBAC
        permission checks, which are permission-code-based, not
        role-name-based) -- confirmed production incident. An exact-cased
        `role_name` argument still matches exactly as before; this only
        ever adds matches, never removes one.
        """
        stmt = (
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.name.ilike(role_name))
            .order_by(User.name)
        )
        if active_only:
            stmt = stmt.where(User.is_active.is_(True))
        if team_leader_id is not None:
            stmt = stmt.where(User.team_leader_id == team_leader_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_jti(self, jti: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.jti == jti)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
