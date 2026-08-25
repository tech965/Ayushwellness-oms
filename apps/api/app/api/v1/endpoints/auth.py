"""Login, refresh, logout, current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.auth import User
from app.schemas.auth import (
    AccessTokenResponse,
    CurrentUserResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
)
from app.schemas.response import ApiResponse
from app.services.auth_service import AuthService

router = APIRouter()


@router.post("/login", response_model=ApiResponse[TokenResponse])
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_db)
) -> ApiResponse[TokenResponse]:
    access_token, refresh_token = await AuthService(session).login(
        email=payload.email, password=payload.password
    )
    return ApiResponse(data=TokenResponse(access_token=access_token, refresh_token=refresh_token))


@router.post("/refresh", response_model=ApiResponse[AccessTokenResponse])
async def refresh(
    payload: RefreshRequest, session: AsyncSession = Depends(get_db)
) -> ApiResponse[AccessTokenResponse]:
    access_token = await AuthService(session).refresh(refresh_token=payload.refresh_token)
    return ApiResponse(data=AccessTokenResponse(access_token=access_token))


@router.post("/logout", response_model=ApiResponse[None])
async def logout(
    payload: LogoutRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await AuthService(session).logout(refresh_token=payload.refresh_token)
    return ApiResponse(data=None, message="Logged out.")


@router.get("/me", response_model=ApiResponse[CurrentUserResponse])
async def me(current_user: User = Depends(get_current_user)) -> ApiResponse[CurrentUserResponse]:
    return ApiResponse(
        data=CurrentUserResponse(
            id=current_user.id,
            name=current_user.name,
            email=current_user.email,
            phone=current_user.phone,
            is_active=current_user.is_active,
            is_superuser=current_user.is_superuser,
            roles=current_user.role_names,
            permissions=sorted(current_user.permission_codes),
        )
    )
