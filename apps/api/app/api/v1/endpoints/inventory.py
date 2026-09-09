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
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.models.enums import InventoryMovementType
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.inventory import (
    CatalogNameResponse,
    InventoryAdjustmentRequest,
    InventoryMovementResponse,
    InventoryProductStockResponse,
    InventoryProductSummaryResponse,
    InventoryProductVariantsResponse,
    InventoryVariantResponse,
    PacketsPerBoxUpdateRequest,
    ProductNameUpdateRequest,
    ProductStockAdjustmentRequest,
    ProductVariantStockLine,
    VariantNameUpdateRequest,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.inventory_service import InventoryService

router = APIRouter()


def _variant_display_title(variant) -> str:  # noqa: ANN001
    """Custom name if the staff set one, else Shopify's title, else the
    SKU -- never blank, so the UI always has something to show.
    """
    return variant.title_override or variant.title or variant.sku


def _product_display_title(product) -> str:  # noqa: ANN001
    return product.title_override or product.title


def _variant_response(variant, *, threshold: int) -> InventoryVariantResponse:  # noqa: ANN001
    available = variant.available_quantity
    return InventoryVariantResponse(
        id=variant.id,
        product_id=variant.product_id,
        sku=variant.sku,
        variant_title=variant.title,
        variant_title_override=variant.title_override,
        display_title=_variant_display_title(variant),
        packets_per_box=variant.packets_per_box,
        available_boxes=available,
        total_packets=available * variant.packets_per_box,
        stock_status=InventoryService.compute_stock_status(available, threshold),
        status=variant.status,
        shopify_inventory_quantity=variant.inventory_quantity,
        updated_at=variant.updated_at,
    )


def _product_stock_response(  # noqa: ANN001
    product, variants, *, threshold: int
) -> InventoryProductStockResponse:
    """Aggregate a product's underlying `ProductVariant` rows into the ONE
    product-level Inventory card. Boxes are the common unit for every
    variant, so `available_boxes` is a plain sum; `total_packets` sums
    each variant's own `boxes * packets_per_box`. Never a stored record --
    recomputed from live variant rows on every read.
    """
    available_boxes = sum(v.available_quantity for v in variants)
    total_packets = sum(v.available_quantity * v.packets_per_box for v in variants)
    pack_sizes = {v.packets_per_box for v in variants}
    return InventoryProductStockResponse(
        product_id=product.id,
        product_name=_product_display_title(product),
        title=product.title,
        title_override=product.title_override,
        available_boxes=available_boxes,
        total_packets=total_packets,
        stock_status=InventoryService.compute_stock_status(available_boxes, threshold),
        variant_count=len(variants),
        packets_per_box_uniform=len(pack_sizes) <= 1,
        variant_ids=[v.id for v in variants],
        variants=[
            ProductVariantStockLine(
                id=v.id,
                sku=v.sku,
                variant_title=v.title,
                variant_title_override=v.title_override,
                display_title=_variant_display_title(v),
                available_boxes=v.available_quantity,
                packets_per_box=v.packets_per_box,
                total_packets=v.available_quantity * v.packets_per_box,
                stock_status=InventoryService.compute_stock_status(v.available_quantity, threshold),
            )
            for v in variants
        ],
    )


def _catalog_name_response(obj) -> CatalogNameResponse:  # noqa: ANN001
    """Shared shape for the product/variant Edit-Name + Reset-Name
    endpoints -- `obj` is a `Product` or `ProductVariant`.
    """
    return CatalogNameResponse(
        id=obj.id,
        title=obj.title,
        title_override=obj.title_override,
        display_title=obj.title_override or obj.title or getattr(obj, "sku", None) or "",
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
        variant_display_title=_variant_display_title(variant) if variant else None,
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
    session: Any = Depends(get_db),
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
                title_override=product.title_override,
                display_title=_product_display_title(product),
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
    session: Any = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[InventoryProductVariantsResponse]:
    service = InventoryService(session)
    threshold = await service.get_low_stock_threshold()
    product, variants = await service.list_variants_for_product(product_id)
    return ApiResponse(
        data=InventoryProductVariantsResponse(
            product_id=product.id,
            product_title=product.title,
            product_title_override=product.title_override,
            product_display_title=_product_display_title(product),
            variants=[_variant_response(v, threshold=threshold) for v in variants],
        )
    )


@router.get(
    "/products/{product_id}/stock", response_model=ApiResponse[InventoryProductStockResponse]
)
async def get_product_stock(
    product_id: uuid.UUID,
    session: Any = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[InventoryProductStockResponse]:
    """The ONE product-level Inventory card: the product's total OMS stock,
    aggregated live from its underlying `ProductVariant` rows (which are
    kept intact and still carry their own SKU / packets_per_box / ledger).
    """
    service = InventoryService(session)
    threshold = await service.get_low_stock_threshold()
    product, variants = await service.list_variants_for_product(product_id)
    return ApiResponse(data=_product_stock_response(product, variants, threshold=threshold))


@router.get("/stock/{variant_id}", response_model=ApiResponse[InventoryVariantResponse])
async def get_stock(
    variant_id: uuid.UUID,
    session: Any = Depends(get_db),
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
    session: Any = Depends(get_db),
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


@router.post("/products/{product_id}/adjust", response_model=ApiResponse[InventoryMovementResponse])
async def adjust_product_stock(
    product_id: uuid.UUID,
    payload: ProductStockAdjustmentRequest,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[InventoryMovementResponse]:
    """Product-level Edit Stock for a SINGLE-variant product only. A
    multi-variant product returns 422 -- the client edits each variant
    line individually (`POST /stock/{variant_id}/adjust`); no product-
    level distribution rule is invented server-side.
    """
    service = InventoryService(session)
    movement = await service.adjust_product_to_target(
        product_id, target_boxes=payload.target_boxes, reason=payload.reason, actor=current_user
    )
    resolved = await service.movements.get_by_id_with_relations(movement.id)
    assert resolved is not None
    return ApiResponse(data=_movement_response(resolved), message="Stock adjusted.")


@router.post("/stock/{variant_id}/adjust", response_model=ApiResponse[InventoryMovementResponse])
async def adjust_stock(
    variant_id: uuid.UUID,
    payload: InventoryAdjustmentRequest,
    session: Any = Depends(get_db),
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
    session: Any = Depends(get_db),
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


# --- catalog display name (manual "Edit Name") ---------------------------
# Presentation/catalog only -- these set `title_override`, never `title`,
# and never move stock. A later Shopify product sync overwrites `title`
# but not `title_override` (the normalizer doesn't emit that key), so a
# staff edit here persists. Gated on the existing `inventory.manage`
# permission (same as stock adjustment / packets-per-box).


@router.patch("/products/{product_id}/name", response_model=ApiResponse[CatalogNameResponse])
async def set_product_name(
    product_id: uuid.UUID,
    payload: ProductNameUpdateRequest,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[CatalogNameResponse]:
    product = await InventoryService(session).set_product_display_name(
        product_id, name=payload.name, actor=current_user
    )
    return ApiResponse(data=_catalog_name_response(product), message="Product name updated.")


@router.delete("/products/{product_id}/name", response_model=ApiResponse[CatalogNameResponse])
async def reset_product_name(
    product_id: uuid.UUID,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[CatalogNameResponse]:
    """Reset to the Shopify name -- clears `title_override`."""
    product = await InventoryService(session).set_product_display_name(
        product_id, name=None, actor=current_user
    )
    return ApiResponse(data=_catalog_name_response(product), message="Reset to Shopify name.")


@router.patch("/stock/{variant_id}/name", response_model=ApiResponse[CatalogNameResponse])
async def set_variant_name(
    variant_id: uuid.UUID,
    payload: VariantNameUpdateRequest,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[CatalogNameResponse]:
    variant = await InventoryService(session).set_variant_display_name(
        variant_id, name=payload.name, actor=current_user
    )
    return ApiResponse(data=_catalog_name_response(variant), message="Variant name updated.")


@router.delete("/stock/{variant_id}/name", response_model=ApiResponse[CatalogNameResponse])
async def reset_variant_name(
    variant_id: uuid.UUID,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[CatalogNameResponse]:
    """Reset to the Shopify name -- clears `title_override`."""
    variant = await InventoryService(session).set_variant_display_name(
        variant_id, name=None, actor=current_user
    )
    return ApiResponse(data=_catalog_name_response(variant), message="Reset to Shopify name.")
