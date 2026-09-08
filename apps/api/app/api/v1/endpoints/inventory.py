"""Product -> Variant -> Inventory hierarchy, in BOXES.

Writes happen automatically from `InventoryService.apply_dispatch`/
`apply_rto_restock` (see `app.integrations.shiprocket.sync.
apply_tracking_event` and `app.services.rto_service.RTOService.
update_rto`) -- the only staff-initiated writes here are a stock
adjustment and a packets-per-box change. Shopify is never a source or
input for anything in this module (see `InventoryService`'s docstring).
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
from app.models.enums import InventoryMovementType
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.inventory import (
    InventoryAdjustmentRequest,
    InventoryMovementResponse,
    InventoryProductSummaryResponse,
    InventoryProductVariantsResponse,
    InventoryVariantResponse,
    PacketsPerBoxUpdateRequest,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.inventory_service import InventoryService

router = APIRouter()


def _variant_response(variant, *, threshold: int) -> InventoryVariantResponse:  # noqa: ANN001
    available = variant.available_quantity
    return InventoryVariantResponse(
        id=variant.id,
        product_id=variant.product_id,
        sku=variant.sku,
        variant_title=variant.title,
        packets_per_box=variant.packets_per_box,
        available_boxes=available,
        total_packets=available * variant.packets_per_box,
        stock_status=InventoryService.compute_stock_status(available, threshold),
        status=variant.status,
        shopify_inventory_quantity=variant.inventory_quantity,
        updated_at=variant.updated_at,
    )


def _movement_response(movement) -> InventoryMovementResponse:  # noqa: ANN001
    variant = movement.product_variant
    product = variant.product if variant else None
    if movement.actor is not None:
        actor_label = movement.actor.name
    elif movement.movement_type in (
        InventoryMovementType.DISPATCH,
        InventoryMovementType.RTO_RESTOCK,
    ):
        actor_label = "Shiprocket"
    else:
        actor_label = "System"

    return InventoryMovementResponse(
        id=movement.id,
        product_variant_id=movement.product_variant_id,
        product_id=product.id if product else None,
        product_title=product.title if product else None,
        variant_title=variant.title if variant else None,
        sku=variant.sku if variant else None,
        movement_type=movement.movement_type,
        quantity_delta=movement.quantity_delta,
        previous_balance=movement.quantity_after - movement.quantity_delta,
        quantity_after=movement.quantity_after,
        order_id=movement.order_id,
        shipment_id=movement.shipment_id,
        rto_id=movement.rto_id,
        actor_user_id=movement.actor_user_id,
        actor_label=actor_label,
        reason=movement.reason,
        notes=movement.notes,
        created_at=movement.created_at,
    )


@router.get("/stock", response_model=PaginatedResponse[InventoryProductSummaryResponse])
async def list_product_stock(
    q: str | None = Query(default=None, description="Search by product name, vendor, or SKU."),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> PaginatedResponse[InventoryProductSummaryResponse]:
    """Main Inventory page: one row per PRODUCT (not per SKU) -- click
    through to `GET /inventory/products/{product_id}/variants` for the
    Product -> Variant -> Inventory drill-down.
    """
    service = InventoryService(session)
    threshold = await service.get_low_stock_threshold()
    products, total = await service.list_products(
        page_params=page_params, sort_params=sort_params, q=q
    )

    data = []
    for product in products:
        variants = product.variants
        total_boxes = sum(v.available_quantity for v in variants)
        total_packets = sum(v.available_quantity * v.packets_per_box for v in variants)
        worst_status = InventoryService.compute_stock_status(total_boxes, threshold)
        data.append(
            InventoryProductSummaryResponse(
                id=product.id,
                title=product.title,
                vendor=product.vendor,
                variant_count=len(variants),
                total_available_boxes=total_boxes,
                total_packets=total_packets,
                stock_status=worst_status,
                updated_at=product.updated_at,
            )
        )

    return PaginatedResponse(
        data=data, meta=build_pagination_meta(total_items=total, page_params=page_params)
    )


@router.get(
    "/products/{product_id}/variants", response_model=ApiResponse[InventoryProductVariantsResponse]
)
async def list_product_variants(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[InventoryProductVariantsResponse]:
    service = InventoryService(session)
    threshold = await service.get_low_stock_threshold()
    product, variants = await service.list_variants_for_product(product_id)
    return ApiResponse(
        data=InventoryProductVariantsResponse(
            product_id=product.id,
            product_title=product.title,
            variants=[_variant_response(v, threshold=threshold) for v in variants],
        )
    )


@router.get("/stock/{variant_id}", response_model=ApiResponse[InventoryVariantResponse])
async def get_stock(
    variant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[InventoryVariantResponse]:
    service = InventoryService(session)
    threshold = await service.get_low_stock_threshold()
    variant = await service.get_variant_stock(variant_id)
    return ApiResponse(data=_variant_response(variant, threshold=threshold))


@router.get("/movements", response_model=PaginatedResponse[InventoryMovementResponse])
async def list_movements(
    product_variant_id: uuid.UUID | None = Query(default=None),
    product_id: uuid.UUID | None = Query(default=None),
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
        product_id=product_id,
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
    service = InventoryService(session)
    movement = await service.adjust_to_target(
        variant_id, target_boxes=payload.target_boxes, reason=payload.reason, actor=current_user
    )
    # Re-fetch with relationships eagerly loaded so the response can
    # include product/variant/actor labels the same way the list endpoint
    # does -- `adjust_to_target` returns the bare, just-created row.
    resolved = await service.movements.get_by_id_with_relations(movement.id)
    assert resolved is not None  # just committed in the same session, above
    return ApiResponse(data=_movement_response(resolved), message="Stock adjusted.")


@router.patch("/stock/{variant_id}/settings", response_model=ApiResponse[InventoryVariantResponse])
async def update_variant_settings(
    variant_id: uuid.UUID,
    payload: PacketsPerBoxUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[InventoryVariantResponse]:
    service = InventoryService(session)
    await service.update_packets_per_box(
        variant_id, packets_per_box=payload.packets_per_box, actor=current_user
    )
    threshold = await service.get_low_stock_threshold()
    variant = await service.get_variant_stock(variant_id)
    return ApiResponse(
        data=_variant_response(variant, threshold=threshold), message="Packets per box updated."
    )
