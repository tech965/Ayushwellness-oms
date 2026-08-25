"""integrations endpoints.

Status: Phase 2.1 — monitoring/health only. Connecting a real provider
(OAuth flow, credential entry) is Phase 2.
"""

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
from app.schemas.integration import (
    IntegrationHealthResponse,
    IntegrationResponse,
    SyncJobResponse,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.integration_service import IntegrationService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[IntegrationResponse])
async def list_integrations(
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("integrations.read")),
) -> PaginatedResponse[IntegrationResponse]:
    items, total = await IntegrationService(session).list_integrations(
        page_params=page_params, sort_params=sort_params
    )
    return PaginatedResponse(
        data=[IntegrationResponse.model_validate(i) for i in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/{integration_id}", response_model=ApiResponse[IntegrationResponse])
async def get_integration(
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("integrations.read")),
) -> ApiResponse[IntegrationResponse]:
    integration = await IntegrationService(session).get_integration(integration_id)
    return ApiResponse(data=IntegrationResponse.model_validate(integration))


@router.get("/{integration_id}/health", response_model=ApiResponse[IntegrationHealthResponse])
async def get_integration_health(
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("integrations.read")),
) -> ApiResponse[IntegrationHealthResponse]:
    service = IntegrationService(session)
    integration = await service.get_integration(integration_id)
    health = await service.get_health(integration_id)
    return ApiResponse(
        data=IntegrationHealthResponse(
            connected=health.connected,
            status=integration.status.value,
            response_time_ms=health.response_time_ms,
            last_successful_sync_at=health.last_successful_at,
            last_failure_at=health.last_failure_at,
            error_message=health.error_message,
        )
    )


@router.post(
    "/{integration_id}/health-check", response_model=ApiResponse[IntegrationHealthResponse]
)
async def run_integration_health_check(
    integration_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("integrations.test")),
) -> ApiResponse[IntegrationHealthResponse]:
    """Actively probes the registered adapter (Shopify as of Phase 2.2;
    "not configured" if no credentials are set), and persists the result.
    """
    service = IntegrationService(session)
    integration = await service.get_integration(integration_id)
    health = await service.run_health_check(integration_id, actor=current_user)
    return ApiResponse(
        data=IntegrationHealthResponse(
            connected=health.connected,
            status=integration.status.value,
            response_time_ms=health.response_time_ms,
            last_successful_sync_at=health.last_successful_at,
            last_failure_at=health.last_failure_at,
            error_message=health.error_message,
        ),
        message="Health check complete.",
    )


@router.get("/{integration_id}/sync-history", response_model=PaginatedResponse[SyncJobResponse])
async def get_integration_sync_history(
    integration_id: uuid.UUID,
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("integrations.read")),
) -> PaginatedResponse[SyncJobResponse]:
    items, total = await IntegrationService(session).get_sync_history(
        integration_id, page_params=page_params, sort_params=sort_params
    )
    return PaginatedResponse(
        data=[SyncJobResponse.model_validate(j) for j in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )
