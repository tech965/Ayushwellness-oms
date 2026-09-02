"""OMS Settings (Administration -> Settings). See
`app.services.settings_service` for the single-row/JSON-blob storage
rationale and `app.schemas.settings` for the section shapes.

`GET` only requires authentication (not `settings.read`) -- nothing in
`AppSettingsResponse` is sensitive, and several values (page size,
dashboard refresh interval, session timeout) are applied in the
background on every page for every role, not just whoever can see the
Administration -> Settings screen (that visibility is a `settings.read`
nav-level gate on the frontend, same convention as `Users`/`Roles`).
`PUT` stays behind `settings.manage`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import get_current_user, require_permission
from app.models.auth import User
from app.schemas.response import ApiResponse
from app.schemas.settings import AppSettingsResponse, AppSettingsUpdateRequest
from app.services.settings_service import SettingsService

router = APIRouter()


@router.get("", response_model=ApiResponse[AppSettingsResponse])
async def get_settings(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ApiResponse[AppSettingsResponse]:
    settings = await SettingsService(session).get_settings()
    return ApiResponse(data=settings)


@router.put("", response_model=ApiResponse[AppSettingsResponse])
async def update_settings(
    payload: AppSettingsUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("settings.manage")),
) -> ApiResponse[AppSettingsResponse]:
    settings = await SettingsService(session).update_settings(payload, actor_id=current_user.id)
    return ApiResponse(data=settings, message="Settings updated.")
