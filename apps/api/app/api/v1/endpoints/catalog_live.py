"""Live Shopify catalogue (products/variants) joined to OMS-owned stock,
provider-variant mappings, and OMS inventory overview/reservations.

Catalogue fields are read from Shopify per request and never stored. Stock
is OMS-owned and never overwritten from Shopify data."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.models.inventory_architecture import InventoryReservation
from app.schemas.response import ApiResponse
from app.services.audit_service import AuditService
from app.services.inventory_reservation_service import (
    InventoryReservationService,
    VariantMappingService,
    stock_position,
)
from app.services.live_catalog import PROVIDER, LiveCatalogService

router = APIRouter()


class MappingRequest(BaseModel):
    external_variant_id: str
    variant_id: uuid.UUID
    external_product_id: str | None = None


@router.get("/products", response_model=ApiResponse[dict])
async def list_live_products(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    sku: str | None = Query(default=None, max_length=120),
    status: str | None = Query(
        default=None, pattern="^(active|draft|archived|ACTIVE|DRAFT|ARCHIVED)$"
    ),
    mapped: bool | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[dict[str, Any]]:
    data = await LiveCatalogService(session).list_products(
        cursor=cursor, limit=limit, q=q, sku=sku, status=status, mapped=mapped
    )
    return ApiResponse(data=data)


@router.get("/products/{external_product_id}", response_model=ApiResponse[dict])
async def get_live_product(
    external_product_id: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[dict[str, Any]]:
    product = await LiveCatalogService(session).get_product(external_product_id)
    if product is None:
        raise NotFoundError("Product not found in Shopify.")
    return ApiResponse(data=product)


@router.get("/variants/{external_variant_id}", response_model=ApiResponse[dict])
async def get_live_variant(
    external_variant_id: str,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[dict[str, Any]]:
    variant = await LiveCatalogService(session).get_variant(external_variant_id)
    if variant is None:
        raise NotFoundError(
            "Variant not found. Only variants already mapped to an OMS variant can be opened "
            "directly; map it first."
        )
    return ApiResponse(data=variant)


@router.post("/mappings", response_model=ApiResponse[dict], status_code=201)
async def map_variant(
    payload: MappingRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[dict[str, Any]]:
    mapping = await VariantMappingService(session).map(
        provider=PROVIDER,
        external_variant_id=payload.external_variant_id,
        external_product_id=payload.external_product_id,
        variant_id=payload.variant_id,
    )
    await AuditService(session).record(
        user=current_user,
        action="inventory.variant_mapped",
        entity_type="product_variant",
        entity_id=str(payload.variant_id),
        new_value={"provider": PROVIDER, "external_variant_id": payload.external_variant_id},
    )
    await session.commit()
    return ApiResponse(
        data={
            "id": str(mapping.id),
            "variant_id": str(mapping.variant_id),
            "external_variant_id": mapping.external_variant_id,
        }
    )


@router.post("/mappings/auto-map-by-sku", response_model=ApiResponse[dict])
async def auto_map_by_sku(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[dict[str, Any]]:
    """Maps live Shopify variants whose SKU exactly equals an OMS variant's
    SKU. Unmatched ones are reported, never guessed."""
    result = await LiveCatalogService(session).auto_map_by_sku()
    await AuditService(session).record(
        user=current_user,
        action="inventory.variants_auto_mapped",
        entity_type="product_variant",
        entity_id="batch",
        new_value={"mapped": len(result["mapped"]), "unmatched": len(result["unmatched"])},
    )
    await session.commit()
    return ApiResponse(data=result)


@router.get("/inventory/overview", response_model=ApiResponse[dict])
async def inventory_overview(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    q: str | None = Query(default=None, max_length=120),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[dict[str, Any]]:
    return ApiResponse(
        data=await LiveCatalogService(session).overview(page=page, page_size=page_size, q=q)
    )


@router.get("/inventory/variants/{variant_id}/position", response_model=ApiResponse[dict])
async def variant_position(
    variant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("inventory.read")),
) -> ApiResponse[dict[str, Any]]:
    position = await stock_position(session, variant_id)
    reservations = (
        (
            await session.execute(
                select(InventoryReservation)
                .where(InventoryReservation.variant_id == variant_id)
                .order_by(InventoryReservation.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return ApiResponse(
        data={
            "variant_id": str(variant_id),
            "physical_quantity": position.physical,
            "reserved_quantity": position.reserved,
            "available_to_promise": position.available_to_promise,
            "reservations": [
                {
                    "order_id": str(r.order_id),
                    "quantity": r.quantity,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                }
                for r in reservations
            ],
        }
    )


@router.post("/inventory/orders/{order_id}/release-reservations", response_model=ApiResponse[dict])
async def release_order_reservations(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("inventory.manage")),
) -> ApiResponse[dict[str, Any]]:
    """Manually release an order's active reservations (e.g. a shipment that
    was abandoned). Idempotent."""
    released = await InventoryReservationService(session).release(order_id)
    await AuditService(session).record(
        user=current_user,
        action="inventory.reservations_released",
        entity_type="order",
        entity_id=str(order_id),
        new_value={"released": released},
    )
    await session.commit()
    return ApiResponse(data={"released": released})
