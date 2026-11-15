"""User management. All routes require `users.manage`."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.user import (
    PasswordResetRequest,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.user_service import UserService

router = APIRouter()


def _to_response(user: User) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True).model_copy(
        update={"roles": user.role_names}
    )


async def _audit(session: AsyncSession, actor: User, action: str, target: User, data: dict) -> None:
    """Never include passwords or hashes in `data`."""
    await AuditService(session).record(
        user=actor, action=action, entity_type="user", entity_id=str(target.id), new_value=data
    )
    await session.commit()


@router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("users.manage")),
) -> PaginatedResponse[UserResponse]:
    items, total = await UserService(session).list_users(
        page_params=page_params, sort_params=sort_params
    )
    return PaginatedResponse(
        data=[_to_response(u) for u in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("", response_model=ApiResponse[UserResponse], status_code=201)
async def create_user(
    payload: UserCreateRequest,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users.manage")),
) -> ApiResponse[UserResponse]:
    user = await UserService(session).create_user(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        password=payload.password,
        role_ids=payload.role_ids,
        team_leader_id=payload.team_leader_id,
        shipment_staff_id=payload.shipment_staff_id,
    )
    await _audit(session, actor, "user.created", user, {"email": user.email, "roles": user.role_names})
    return ApiResponse(data=_to_response(user), message="User created.")


@router.get("/{user_id}", response_model=ApiResponse[UserResponse])
async def get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("users.manage")),
) -> ApiResponse[UserResponse]:
    user = await UserService(session).get_user(user_id)
    return ApiResponse(data=_to_response(user))


@router.patch("/{user_id}", response_model=ApiResponse[UserResponse])
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users.manage")),
) -> ApiResponse[UserResponse]:
    user = await UserService(session).update_user(
        user_id,
        name=payload.name,
        phone=payload.phone,
        is_active=payload.is_active,
        role_ids=payload.role_ids,
        team_leader_id=payload.team_leader_id,
        clear_team_leader=payload.clear_team_leader,
        shipment_staff_id=payload.shipment_staff_id,
        clear_shipment_staff=payload.clear_shipment_staff,
    )
    changed = payload.model_dump(exclude_unset=True, exclude={"role_ids"})
    if payload.role_ids is not None:
        changed["roles"] = user.role_names
    await _audit(session, actor, "user.updated", user, changed)
    return ApiResponse(data=_to_response(user), message="User updated.")


@router.delete("/{user_id}", response_model=ApiResponse[None])
async def deactivate_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users.manage")),
) -> ApiResponse[None]:
    """Master data is deactivated, never hard-deleted (spec §31)."""
    user = await UserService(session).deactivate_user(user_id)
    await _audit(session, actor, "user.deactivated", user, {"is_active": False})
    return ApiResponse(data=None, message="User deactivated.")


@router.post("/{user_id}/reset-password", response_model=ApiResponse[None])
async def reset_password(
    user_id: uuid.UUID,
    payload: PasswordResetRequest,
    session: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users.manage")),
) -> ApiResponse[None]:
    """Admin sets a new password; the user's active sessions are revoked."""
    user = await UserService(session).reset_password(user_id, payload.new_password)
    await _audit(session, actor, "user.password_reset", user, {"sessions_revoked": True})
    return ApiResponse(data=None, message="Password reset.")
