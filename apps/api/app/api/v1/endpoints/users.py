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
from app.schemas.user import UserCreateRequest, UserResponse, UserUpdateRequest
from app.services.user_service import UserService

router = APIRouter()


def _to_response(user: User) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True).model_copy(
        update={"roles": user.role_names}
    )


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
    _: User = Depends(require_permission("users.manage")),
) -> ApiResponse[UserResponse]:
    user = await UserService(session).create_user(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        password=payload.password,
        role_ids=payload.role_ids,
    )
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
    _: User = Depends(require_permission("users.manage")),
) -> ApiResponse[UserResponse]:
    user = await UserService(session).update_user(
        user_id,
        name=payload.name,
        phone=payload.phone,
        is_active=payload.is_active,
        role_ids=payload.role_ids,
    )
    return ApiResponse(data=_to_response(user), message="User updated.")


@router.delete("/{user_id}", response_model=ApiResponse[None])
async def deactivate_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("users.manage")),
) -> ApiResponse[None]:
    """Master data is deactivated, never hard-deleted (spec §31)."""
    await UserService(session).deactivate_user(user_id)
    return ApiResponse(data=None, message="User deactivated.")
