"""Multi-platform (Amazon/Flipkart/Blinkit/Meesho/Manual + Shopify)
inventory schemas. All quantities are in BOXES, same unit as
`app.schemas.inventory` (see that module's docstring) — a platform-stock
number and a Shopify `available_boxes` number are always directly
comparable, never packets-vs-boxes mismatched.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PlatformStockMovementType
from app.models.platform_inventory import InventoryPlatform


class PlatformStockMovementCreateRequest(BaseModel):
    """Record ONE manual platform stock movement — staff enters only the
    quantity being added or deducted, never the resulting total (mirrors
    `InventoryAdjustmentRequest`'s existing additive-only contract
    exactly; see `PlatformInventoryService.record_movement`). `reason` is
    optional here (unlike the existing Shopify adjustment's mandatory
    reason) — a deliberate difference, not an oversight.
    """

    platform: str = Field(max_length=50)
    movement_type: Literal["stock_added", "stock_deducted"]
    quantity: int = Field(gt=0, description="Quantity being added or deducted, in boxes.")
    reason: str | None = Field(default=None, max_length=255)
    stock_date: date | None = Field(
        default=None,
        description="IST business date this movement belongs to. Defaults to today (IST).",
    )

    @field_validator("platform")
    @classmethod
    def _validate_platform(cls, value: str) -> str:
        if value not in InventoryPlatform.ALL:
            allowed = ", ".join(InventoryPlatform.ALL)
            raise ValueError(f"Unknown platform {value!r}. Must be one of: {allowed}.")
        return value


class PlatformStockMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_variant_id: uuid.UUID
    platform: str
    platform_label: str
    movement_type: PlatformStockMovementType
    quantity_delta: int
    quantity_after: int
    stock_date: date
    reason: str | None
    actor_user_id: uuid.UUID | None
    actor_label: str
    created_at: datetime


class UnifiedStockMovementResponse(BaseModel):
    """One row of the merged "Platform Stock Movement History" (this
    product variant's `PlatformStockMovement` rows interleaved with its
    existing Shopify `InventoryMovement` rows, sorted by time) — the
    existing standalone Shopify movement-history table/endpoint is
    completely untouched; this is an ADDITIONAL, read-only merged view.
    """

    id: uuid.UUID
    platform: str
    platform_label: str
    movement_type: str
    quantity_delta: int
    quantity_after: int
    stock_date: date
    reason: str | None
    actor_label: str
    created_at: datetime


class PlatformStockSummaryRow(BaseModel):
    """One platform's row in the "Marketplace Stock" table for one
    product variant, on the selected `stock_date`.

    `is_automatic=True` (Shopify) only: `opening_stock` is always `None`
    and `current_stock` is always the LIVE value (`ProductVariant.
    available_quantity`) regardless of `stock_date` — Shopify has no
    historical day-by-day balance snapshot in this codebase (only a live
    total + a movement ledger), so a past date's Shopify BALANCE is never
    fabricated; only its real, date-scoped `stock_added`/`stock_deducted`
    movement totals are shown for a past date.

    `is_automatic=False` (Amazon/Flipkart/Blinkit/Meesho/Manual):
    `current_stock` is the true closing balance AS OF `stock_date`
    (`PlatformStockMovementRepository.get_latest_as_of`), and
    `opening_stock` is that same balance as of the day before.
    """

    platform: str
    platform_label: str
    is_automatic: bool
    opening_stock: int | None
    stock_added: int
    stock_deducted: int
    current_stock: int
    last_updated: datetime | None


class VariantPlatformStockResponse(BaseModel):
    product_variant_id: uuid.UUID
    sku: str
    variant_title: str | None
    stock_date: date
    platforms: list[PlatformStockSummaryRow]


class ProductPlatformStockResponse(BaseModel):
    product_id: uuid.UUID
    product_title: str
    stock_date: date
    variants: list[VariantPlatformStockResponse]


class ShipmentTransitSummaryRow(BaseModel):
    """One variant's shipment-status breakdown, derived entirely from the
    existing `InventoryMovement` (DISPATCH rows already link
    `product_variant_id` + `shipment_id`) joined to `Shipment.
    current_status` — never a fabricated/estimated count.

    `in_transit`/`out_for_delivery`/`rto` are LIVE current snapshot
    counts, not scoped to `stock_date` — a shipment's status has no
    historical day-by-day record in this codebase, so filtering these by
    a past date would silently show today's numbers under a stale label,
    which Requirement 10 explicitly forbids. Only `delivered_on_date` is
    genuinely date-scoped (by `Shipment.actual_delivery_date`).
    """

    product_variant_id: uuid.UUID
    sku: str
    in_transit: int
    out_for_delivery: int
    delivered_on_date: int
    rto: int


class ProductShipmentSummaryResponse(BaseModel):
    product_id: uuid.UUID
    stock_date: date
    in_transit: int
    out_for_delivery: int
    delivered_on_date: int
    rto: int
    variants: list[ShipmentTransitSummaryRow]
