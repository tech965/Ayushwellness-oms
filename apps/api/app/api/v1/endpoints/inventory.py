"""Stock levels + movement ledger. Writes happen automatically from
`InventoryService.apply_dispatch`/`apply_rto_restock` (see
`app.integrations.shiprocket.sync.apply_tracking_event` and
`app.services.rto_service.RTOService.update_rto`) -- the only
staff-initiated write here is a manual adjustment.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.inventory import (
    InventoryAdjustmentRequest,
    InventoryMovementResponse,
    InventoryStockResponse,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.inventory_service import InventoryService

router = APIRouter()


def _stock_response(variant) -> InventoryStockResponse:  # noqa: ANN001
    return InventoryStockResponse(
        id=variant.id,
        product_id=variant.product_id,
        sku=variant.sku,
        variant_title=variant.title,
        product_title=variant.product.title,
        available_quantity=variant.available_quantity,
        inventory_quantity=variant.inventory_quantity,
        status=variant.status,
        updated_at=variant.updated_at,
    )


def _movement_response(movement) -> InventoryMovementResponse:  # noqa: ANN001
    return InventoryMovementResponse(
        id=movement.id,
        product_variant_id=movement.product_variant_id,
        sku=movement.product_variant.sku if movement.product_variant else None,
        movement_type=movement.movement_type,
        quantity_delta=movement.quantity_delta,
        quantity_after=movement.quantity_after,
        order_id=movement.order_id,
        shipment_id=movement.shipment_id,
        rto_id=movement.rto_id,
        actor_user_id=movement.actor_user_id,
        reason=movement.reason,
        notes=movement.notes,
        created_at=movement.created_at,
    )


@router.get("/stock", response_model=PaginatedResponse[InventoryStockResponse])
async def list_stock(
    q: str | None = Query(default=None),
    low_stock_only: bool = Query(default=False),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> PaginatedResponse[InventoryStockResponse]:
    items, total = await InventoryService(session).list_stock(
        page_params=page_params, sort_params=sort_params, q=q, low_stock_only=low_stock_only
    )
    return PaginatedResponse(
        data=[_stock_response(v) for v in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/stock/{variant_id}", response_model=ApiResponse[InventoryStockResponse])
async def get_stock(
    variant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[InventoryStockResponse]:
    variant = await InventoryService(session).get_variant_stock(variant_id)
    return ApiResponse(data=_stock_response(variant))


@router.get("/movements", response_model=PaginatedResponse[InventoryMovementResponse])
async def list_movements(
    product_variant_id: uuid.UUID | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
    movement_type: str | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> PaginatedResponse[InventoryMovementResponse]:
    items, total = await InventoryService(session).list_movements(
        page_params=page_params,
        sort_params=sort_params,
        product_variant_id=product_variant_id,
        order_id=order_id,
        movement_type=movement_type,
    )
    return PaginatedResponse(
        data=[_movement_response(m) for m in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("/stock/{variant_id}/adjust", response_model=ApiResponse[InventoryMovementResponse])
async def adjust_stock(
    variant_id: uuid.UUID,
    payload: InventoryAdjustmentRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[InventoryMovementResponse]:
    movement = await InventoryService(session).adjust_manual(
        variant_id, delta=payload.delta, reason=payload.reason, actor=current_user
    )
    return ApiResponse(data=_movement_response(movement), message="Stock adjusted.")
