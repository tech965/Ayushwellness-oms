"""Role management. Not in spec's literal endpoint list, but `roles.manage`
permission (spec §9) is meaningless without a place to view/edit roles —
see `app.services.rbac_service` docstring.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.models.rbac import Role
from app.schemas.rbac import RoleCreateRequest, RoleResponse, RoleUpdateRequest
from app.schemas.response import ApiResponse
from app.services.rbac_service import RBACService

router = APIRouter()


def _to_response(role: Role) -> RoleResponse:
    return RoleResponse.model_validate(role, from_attributes=True).model_copy(
        update={"permissions": [rp.permission.code for rp in role.role_permissions]}
    )


@router.get("", response_model=ApiResponse[list[RoleResponse]])
async def list_roles(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("roles.manage")),
) -> ApiResponse[list[RoleResponse]]:
    roles = await RBACService(session).list_roles()
    return ApiResponse(data=[_to_response(r) for r in roles])


@router.post("", response_model=ApiResponse[RoleResponse], status_code=201)
async def create_role(
    payload: RoleCreateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("roles.manage")),
) -> ApiResponse[RoleResponse]:
    role = await RBACService(session).create_role(
        name=payload.name, description=payload.description, permission_ids=payload.permission_ids
    )
    return ApiResponse(data=_to_response(role), message="Role created.")


@router.get("/{role_id}", response_model=ApiResponse[RoleResponse])
async def get_role(
    role_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("roles.manage")),
) -> ApiResponse[RoleResponse]:
    role = await RBACService(session).get_role(role_id)
    return ApiResponse(data=_to_response(role))


@router.patch("/{role_id}", response_model=ApiResponse[RoleResponse])
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("roles.manage")),
) -> ApiResponse[RoleResponse]:
    role = await RBACService(session).update_role(
        role_id, description=payload.description, permission_ids=payload.permission_ids
    )
    return ApiResponse(data=_to_response(role), message="Role updated.")
