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
    CatalogVariantNameUpdateRequest,
    CatalogVariantStockAdjustmentResponse,
    InventoryAdjustmentRequest,
    InventoryMovementResponse,
    InventoryProductStockResponse,
    InventoryProductSummaryResponse,
    InventoryProductVariantsResponse,
    InventoryVariantResponse,
    OmsCatalogVariantResponse,
    PacketsPerBoxUpdateRequest,
    PackSizeUpdateRequest,
    ProductNameUpdateRequest,
    ProductStockAdjustmentRequest,
    ProductVariantStockLine,
    VariantNameUpdateRequest,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.inventory_service import InventoryService, OmsVariantGroup

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
        pack_size=variant.pack_size,
        available_boxes=available,
        total_packets=available * variant.packets_per_box,
        stock_status=InventoryService.compute_stock_status(available, threshold),
        status=variant.status,
        shopify_inventory_quantity=variant.inventory_quantity,
        updated_at=variant.updated_at,
    )


def _variant_stock_line(variant, *, threshold: int) -> ProductVariantStockLine:  # noqa: ANN001
    return ProductVariantStockLine(
        id=variant.id,
        sku=variant.sku,
        variant_title=variant.title,
        variant_title_override=variant.title_override,
        display_title=_variant_display_title(variant),
        available_boxes=variant.available_quantity,
        packets_per_box=variant.packets_per_box,
        pack_size=variant.pack_size,
        total_packets=variant.available_quantity * variant.packets_per_box,
        stock_status=InventoryService.compute_stock_status(variant.available_quantity, threshold),
        image_url=variant.image_url,
    )


def _resolve_oms_variant_image(members, product_image_url: str | None) -> str | None:  # noqa: ANN001
    """Explicit Shopify variant/image association ONLY -- the underlying
    `ProductVariant` with the lexicographically smallest `sku` among
    those that have their own `image_url` (Shopify actually assigned one
    a distinct photo), else the product's featured image, else null.
    Never infers which member "should" own an image from ordering,
    filename, or any other guess.

    Sorted by `sku` rather than iterated in whatever order `members`
    arrives in: `ProductVariantRepository.list_for_product` has no
    `ORDER BY`, so raw DB scan order is not guaranteed stable across
    reads/engines. `sku` is unique and always present, so this makes
    "the first one with an image" a well-defined, reproducible rule
    instead of one that could vary between two reads of identical data.
    """
    candidates = [v for v in members if v.image_url]
    if not candidates:
        return product_image_url
    return min(candidates, key=lambda v: v.sku).image_url


def _oms_variant_response(
    group: OmsVariantGroup,
    *,
    threshold: int,
    product_image_url: str | None,
    adjustment_total: int = 0,
) -> OmsCatalogVariantResponse:
    """Aggregate ONE OMS-visible variant from its underlying Shopify
    `ProductVariant` rows. `available_boxes` is a plain SUM of boxes (the
    common unit) PLUS `adjustment_total` -- the cumulative total the
    group's own `CatalogVariantStockAdjustment` ledger has recorded (see
    that model's docstring), 0 for an implicit (ungrouped) OMS variant,
    which can never have one. `total_packets` is deliberately NOT
    adjusted the same way: a reconciliation total has no pack size, so
    there is no packets-per-box to convert it with -- it stays a plain
    SUM of each real row's own `boxes * packets_per_box`.
    `packets_per_box_uniform` is False when the grouped rows disagree --
    NO single conversion ratio is invented.
    """
    members = group.variants
    available_boxes = sum(v.available_quantity for v in members) + adjustment_total
    total_packets = sum(v.available_quantity * v.packets_per_box for v in members)
    pack_sizes = {v.packets_per_box for v in members}
    return OmsCatalogVariantResponse(
        catalog_variant_id=group.catalog_variant_id,
        name=group.name,
        display_order=group.display_order,
        is_active=group.is_active,
        available_boxes=available_boxes,
        total_packets=total_packets,
        stock_status=InventoryService.compute_stock_status(available_boxes, threshold),
        packets_per_box_uniform=len(pack_sizes) <= 1,
        underlying_variant_count=len(members),
        underlying_variants=[_variant_stock_line(v, threshold=threshold) for v in members],
        image_url=_resolve_oms_variant_image(members, product_image_url),
    )


def _product_stock_response(  # noqa: ANN001
    product, oms_groups, *, threshold: int, adjustment_totals: dict | None = None
) -> InventoryProductStockResponse:
    """Product detail payload. `oms_variants` is the only variant view the
    UI shows (3 for Aayush Herbal Masala, 1 for every other grouped
    product). Product totals sum across EVERY underlying `ProductVariant`
    PLUS every CatalogVariant's own reconciliation total (`adjustment_totals`,
    keyed by `catalog_variant_id` -- see `_oms_variant_response`), so the
    product-level header always agrees with the sum of the OMS-variant
    cards shown below it. `total_packets` is not adjusted the same way
    -- see `_oms_variant_response`. Nothing is stored -- recomputed from
    live rows on every read.
    """
    adjustment_totals = adjustment_totals or {}
    all_underlying = [v for g in oms_groups for v in g.variants]
    available_boxes = sum(v.available_quantity for v in all_underlying) + sum(
        adjustment_totals.values()
    )
    total_packets = sum(v.available_quantity * v.packets_per_box for v in all_underlying)
    pack_sizes = {v.packets_per_box for v in all_underlying}
    return InventoryProductStockResponse(
        product_id=product.id,
        shopify_product_id=product.shopify_product_id,
        product_name=_product_display_title(product),
        title=product.title,
        title_override=product.title_override,
        image_url=product.image_url,
        available_boxes=available_boxes,
        total_packets=total_packets,
        stock_status=InventoryService.compute_stock_status(available_boxes, threshold),
        packets_per_box_uniform=len(pack_sizes) <= 1,
        oms_variant_count=len(oms_groups),
        underlying_variant_count=len(all_underlying),
        oms_variants=[
            _oms_variant_response(
                g,
                threshold=threshold,
                product_image_url=product.image_url,
                adjustment_total=adjustment_totals.get(g.catalog_variant_id, 0),
            )
            for g in oms_groups
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


def _catalog_variant_adjustment_response(adjustment) -> CatalogVariantStockAdjustmentResponse:  # noqa: ANN001
    actor_label = adjustment.actor.name if adjustment.actor is not None else "System"
    return CatalogVariantStockAdjustmentResponse(
        id=adjustment.id,
        catalog_variant_id=adjustment.catalog_variant_id,
        quantity_delta=adjustment.quantity_delta,
        quantity_after=adjustment.quantity_after,
        actor_user_id=adjustment.actor_user_id,
        actor_label=actor_label,
        reason=adjustment.reason,
        created_at=adjustment.created_at,
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
        catalog_variant_id=variant.catalog_variant_id if variant else None,
        sku=variant.sku if variant else None,
        movement_type=movement.movement_type,
        quantity_delta=movement.quantity_delta,
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
        # OMS-visible variant count = declared CatalogVariants + every
        # ProductVariant not yet grouped (each of those is its own
        # implicit OMS variant). Relationships eager-loaded by
        # `InventoryProductRepository.search_query`.
        oms_variant_count = len(product.catalog_variants) + sum(
            1 for v in variants if v.catalog_variant_id is None
        )
        data.append(
            InventoryProductSummaryResponse(
                id=product.id,
                title=product.title,
                title_override=product.title_override,
                display_title=_product_display_title(product),
                vendor=product.vendor,
                image_url=product.image_url,
                variant_count=oms_variant_count,
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
    """Product detail: the OMS-visible variants only (3 for Aayush Herbal
    Masala, 1 for every other grouped product), each aggregating its
    underlying Shopify `ProductVariant` rows -- which stay intact and
    keep their own SKU / packets_per_box / movement ledger.
    """
    service = InventoryService(session)
    threshold = await service.get_low_stock_threshold()
    product, oms_groups = await service.get_oms_variants_for_product(product_id)
    adjustment_totals = await service.catalog_variant_adjustments.sum_by_product(product_id)
    return ApiResponse(
        data=_product_stock_response(
            product, oms_groups, threshold=threshold, adjustment_totals=adjustment_totals
        )
    )


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
    catalog_variant_id: uuid.UUID | None = Query(
        default=None,
        description="OMS-visible variant: movements across all its underlying Shopify SKUs.",
    ),
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
        catalog_variant_id=catalog_variant_id,
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
    """Product-level Add Stock for a SINGLE-variant product only. A
    multi-variant product returns 422 -- the client adds stock to each
    variant line individually (`POST /stock/{variant_id}/adjust`); no
    product-level distribution rule is invented server-side.
    """
    service = InventoryService(session)
    movement = await service.add_product_stock(
        product_id,
        quantity_to_add=payload.quantity_to_add,
        reason=payload.reason,
        actor=current_user,
    )
    resolved = await service.movements.get_by_id_with_relations(movement.id)
    assert resolved is not None
    return ApiResponse(data=_movement_response(resolved), message="Stock added.")


@router.post("/stock/{variant_id}/adjust", response_model=ApiResponse[InventoryMovementResponse])
async def adjust_stock(
    variant_id: uuid.UUID,
    payload: InventoryAdjustmentRequest,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[InventoryMovementResponse]:
    service = InventoryService(session)
    movement = await service.add_stock(
        variant_id,
        quantity_to_add=payload.quantity_to_add,
        reason=payload.reason,
        actor=current_user,
    )
    # Re-fetch with relationships eagerly loaded so the response can
    # include product/variant/actor labels the same way the list endpoint
    # does -- `add_stock` returns the bare, just-created row.
    resolved = await service.movements.get_by_id_with_relations(movement.id)
    assert resolved is not None  # just committed in the same session, above
    return ApiResponse(data=_movement_response(resolved), message="Stock added.")


@router.post(
    "/catalog-variants/{catalog_variant_id}/adjust",
    response_model=ApiResponse[CatalogVariantStockAdjustmentResponse],
)
async def adjust_catalog_variant_stock(
    catalog_variant_id: uuid.UUID,
    payload: InventoryAdjustmentRequest,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[CatalogVariantStockAdjustmentResponse]:
    """Add Stock for a multi-SKU OMS-visible variant (e.g. Blue Packet):
    ONE quantity added to the whole CatalogVariant's total, never a
    per-SKU value. Recorded on a separate reconciliation ledger -- see
    `InventoryService.add_catalog_variant_stock` and
    `app.models.product.CatalogVariantStockAdjustment` for why. No
    `ProductVariant` row (available_quantity, SKU, Shopify id) is ever
    touched by this endpoint.
    """
    service = InventoryService(session)
    adjustment = await service.add_catalog_variant_stock(
        catalog_variant_id,
        quantity_to_add=payload.quantity_to_add,
        reason=payload.reason,
        actor=current_user,
    )
    resolved = await service.catalog_variant_adjustments.get_by_id_with_relations(adjustment.id)
    assert resolved is not None  # just committed in the same session, above
    return ApiResponse(data=_catalog_variant_adjustment_response(resolved), message="Stock added.")


@router.get(
    "/catalog-variants/{catalog_variant_id}/adjustments",
    response_model=PaginatedResponse[CatalogVariantStockAdjustmentResponse],
)
async def list_catalog_variant_adjustments(
    catalog_variant_id: uuid.UUID,
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: Any = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> PaginatedResponse[CatalogVariantStockAdjustmentResponse]:
    """History of Total-Stock edits for one OMS-visible variant -- shown
    alongside (not merged into) its underlying SKUs' regular dispatch/
    RTO/manual movement history, which is untouched by this ledger.
    """
    service = InventoryService(session)
    items, total = await service.catalog_variant_adjustments.list(
        page_params=page_params,
        sort_params=sort_params,
        query=service.catalog_variant_adjustments.search_query(
            catalog_variant_id=catalog_variant_id
        ),
        default_sort_column="created_at",
    )
    return PaginatedResponse(
        data=[_catalog_variant_adjustment_response(a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


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


@router.patch("/stock/{variant_id}/pack-size", response_model=ApiResponse[InventoryVariantResponse])
async def update_variant_pack_size(
    variant_id: uuid.UUID,
    payload: PackSizeUpdateRequest,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[InventoryVariantResponse]:
    """How many boxes ONE unit of this variant (as ordered) consumes on
    dispatch -- combined with packets-per-box, drives how many boxes a
    future dispatch/RTO deducts/restores for this SKU (see
    `InventoryService.apply_dispatch`). Never moves `available_boxes`
    itself, and never rewrites past movement history.
    """
    service = InventoryService(session)
    await service.update_pack_size(variant_id, pack_size=payload.pack_size, actor=current_user)
    threshold = await service.get_low_stock_threshold()
    variant = await service.get_variant_stock(variant_id)
    return ApiResponse(
        data=_variant_response(variant, threshold=threshold), message="Pack size updated."
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


@router.patch(
    "/catalog-variants/{catalog_variant_id}/name",
    response_model=ApiResponse[OmsCatalogVariantResponse],
)
async def set_catalog_variant_name(
    catalog_variant_id: uuid.UUID,
    payload: CatalogVariantNameUpdateRequest,
    session: Any = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[OmsCatalogVariantResponse]:
    """Rename an OMS-visible catalog variant (e.g. "Ghutka Flavour").
    Presentation only -- no `ProductVariant`, stock, or ledger row is
    touched, and Shopify sync never reads or writes this name.
    """
    service = InventoryService(session)
    cv = await service.set_catalog_variant_name(
        catalog_variant_id, name=payload.name, actor=current_user
    )
    threshold = await service.get_low_stock_threshold()
    product, oms_groups = await service.get_oms_variants_for_product(cv.product_id)
    group = next(g for g in oms_groups if g.catalog_variant_id == cv.id)
    adjustment_total = await service.catalog_variant_adjustments.sum_for_catalog_variant(cv.id)
    return ApiResponse(
        data=_oms_variant_response(
            group,
            threshold=threshold,
            product_image_url=product.image_url,
            adjustment_total=adjustment_total,
        ),
        message="Catalog variant renamed.",
    )
