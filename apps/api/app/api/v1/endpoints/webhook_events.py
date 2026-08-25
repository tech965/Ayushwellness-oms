"""webhook-events endpoints — read-only monitoring.

Deliberately never returns the raw `payload` (spec §18) — see
`app.schemas.integration.WebhookEventResponse`.
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
from app.schemas.integration import WebhookEventResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.webhook_service import WebhookService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[WebhookEventResponse])
async def list_webhook_events(
    integration_id: uuid.UUID | None = None,
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("webhooks.read")),
) -> PaginatedResponse[WebhookEventResponse]:
    items, total = await WebhookService(session).list_events(
        page_params=page_params, sort_params=sort_params, integration_id=integration_id
    )
    return PaginatedResponse(
        data=[WebhookEventResponse.model_validate(e) for e in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/{webhook_event_id}", response_model=ApiResponse[WebhookEventResponse])
async def get_webhook_event(
    webhook_event_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("webhooks.read")),
) -> ApiResponse[WebhookEventResponse]:
    event = await WebhookService(session).get_event(webhook_event_id)
    return ApiResponse(data=WebhookEventResponse.model_validate(event))
