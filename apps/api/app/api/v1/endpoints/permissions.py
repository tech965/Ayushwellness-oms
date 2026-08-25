"""Read-only permission catalog, for populating role-assignment UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.rbac import PermissionResponse
from app.schemas.response import ApiResponse
from app.services.rbac_service import RBACService

router = APIRouter()


@router.get("", response_model=ApiResponse[list[PermissionResponse]])
async def list_permissions(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("roles.manage")),
) -> ApiResponse[list[PermissionResponse]]:
    permissions = await RBACService(session).list_permissions()
    return ApiResponse(data=[PermissionResponse.model_validate(p) for p in permissions])
