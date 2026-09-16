"""Multi-platform (Amazon/Flipkart/Blinkit/Meesho/Manual) marketplace
stock — a manual, date-wise ledger that sits ALONGSIDE the existing
Shopify/OMS inventory system (`app.services.inventory_service`), never
inside it. See `app.models.platform_inventory.PlatformStockMovement`'s
module docstring for the full "why a separate table" reasoning.

Three hard invariants, mirrored from `InventoryService`:
  1. Shopify inventory (`ProductVariant.available_quantity`,
     `InventoryMovement`) is READ-ONLY from here — this module never
     writes to either. Shopify stays fully automatic.
  2. `record_movement` follows the exact same additive/subtractive,
     never-trust-a-client-total pattern as `InventoryService.add_stock`:
     the caller supplies only the quantity being added/deducted; this
     service reads the current balance and computes the new one itself.
  3. Every write is exactly one `PlatformStockMovement` row (the ledger
     IS the audit trail) plus one `AuditService.record` entry.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.timezone import ist_day_bounds_for_date, ist_today, to_ist
from app.models.enums import InventoryMovementType, PlatformStockMovementType, ShipmentStatus
from app.models.inventory import InventoryMovement
from app.models.platform_inventory import InventoryPlatform, PlatformStockMovement
from app.models.shipment import Shipment
from app.repositories.inventory import InventoryMovementRepository
from app.repositories.platform_inventory import PlatformStockMovementRepository
from app.repositories.product import ProductVariantRepository
from app.schemas.common import PageParams
from app.schemas.platform_inventory import (
    PlatformStockMovementResponse,
    PlatformStockSummaryRow,
    ProductPlatformStockResponse,
    ProductShipmentSummaryResponse,
    ShipmentTransitSummaryRow,
    UnifiedStockMovementResponse,
    VariantPlatformStockResponse,
)
from app.services.audit_service import AuditService
from app.services.inventory_service import InventoryService


class PlatformInventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.movements = PlatformStockMovementRepository(session)
        self.variants = ProductVariantRepository(session)
        self.inventory_movements = InventoryMovementRepository(session)
        self.inventory = InventoryService(session)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------
    # Write: record one manual movement (Add Stock / Record Sale)
    # ------------------------------------------------------------------

    async def record_movement(
        self,
        variant_id: uuid.UUID,
        *,
        platform: str,
        movement_type: PlatformStockMovementType,
        quantity: int,
        reason: str | None,
        stock_date: date_type | None,
        actor,
    ) -> PlatformStockMovement:
        """Add-incoming-stock / record-a-sale manual entry — staff enters
        ONLY the quantity being added or deducted, never the resulting
        total (same contract as `InventoryService.add_stock`). The
        resulting balance is computed here from the actual last-recorded
        balance as of `stock_date`, never trusted from the client.

        A deduction is rejected if it would take the balance below zero
        — same basic sanity `check_stock_available` enforces for Shopify
        dispatch, applied here since there's no automatic marketplace
        sync to catch an impossible negative balance otherwise.
        """
        if quantity <= 0:
            raise ValidationError("Quantity must be a positive number.")
        if platform not in InventoryPlatform.ALL:
            allowed = ", ".join(InventoryPlatform.ALL)
            raise ValidationError(f"Unknown platform {platform!r}. Must be one of: {allowed}.")

        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        resolved_date = stock_date or ist_today()

        latest = await self.movements.get_latest_as_of(
            product_variant_id=variant_id, platform=platform, as_of=resolved_date
        )
        previous_quantity = latest.quantity_after if latest else 0

        if movement_type == PlatformStockMovementType.STOCK_ADDED:
            delta = quantity
        else:
            delta = -quantity
            if previous_quantity + delta < 0:
                raise ValidationError(
                    f"Cannot deduct {quantity} boxes: only {previous_quantity} boxes of "
                    f"{InventoryPlatform.LABELS.get(platform, platform)} stock recorded "
                    f"as of {resolved_date.isoformat()}."
                )

        new_quantity = previous_quantity + delta
        cleaned_reason = reason.strip() if reason and reason.strip() else None

        movement = await self.movements.create(
            product_variant_id=variant_id,
            platform=platform,
            movement_type=movement_type,
            quantity_delta=delta,
            quantity_after=new_quantity,
            stock_date=resolved_date,
            reason=cleaned_reason,
            actor_user_id=actor.id if actor else None,
        )
        await self.audit.record(
            user=actor,
            action="inventory.platform_stock_movement",
            entity_type="product_variant",
            entity_id=str(variant_id),
            previous_value={"platform": platform, "quantity": previous_quantity},
            new_value={"platform": platform, "quantity": new_quantity},
            metadata={
                "movement_type": movement_type.value,
                "stock_date": resolved_date.isoformat(),
                "reason": cleaned_reason,
            },
        )
        await self.session.commit()
        return movement

    # ------------------------------------------------------------------
    # Read: platform stock summary for a date
    # ------------------------------------------------------------------

    async def get_product_platform_stock(
        self, product_id: uuid.UUID, *, stock_date: date_type
    ) -> ProductPlatformStockResponse:
        product, variants = await self.inventory.list_variants_for_product(product_id)
        variant_ids = [v.id for v in variants]
        opening_date = stock_date - timedelta(days=1)

        day_start, day_end = ist_day_bounds_for_date(stock_date)
        shopify_totals = await self.inventory_movements.dispatch_and_restock_totals_bulk(
            product_variant_ids=variant_ids, created_from=day_start, created_to=day_end
        )
        shopify_last_updated = await self.inventory_movements.last_movement_at_bulk(
            product_variant_ids=variant_ids, created_to=day_end
        )

        platform_closing: dict[str, dict[uuid.UUID, PlatformStockMovement]] = {}
        platform_opening: dict[str, dict[uuid.UUID, PlatformStockMovement]] = {}
        platform_day_totals: dict[str, dict[uuid.UUID, tuple[int, int]]] = {}
        for platform in InventoryPlatform.ALL:
            platform_closing[platform] = await self.movements.get_latest_as_of_bulk(
                product_variant_ids=variant_ids, platform=platform, as_of=stock_date
            )
            platform_opening[platform] = await self.movements.get_latest_as_of_bulk(
                product_variant_ids=variant_ids, platform=platform, as_of=opening_date
            )
            platform_day_totals[platform] = await self.movements.sum_for_date_bulk(
                product_variant_ids=variant_ids, platform=platform, stock_date=stock_date
            )

        variant_responses: list[VariantPlatformStockResponse] = []
        for variant in variants:
            rows: list[PlatformStockSummaryRow] = []

            dispatched, restocked = shopify_totals.get(variant.id, (0, 0))
            rows.append(
                PlatformStockSummaryRow(
                    platform="shopify",
                    platform_label="Shopify",
                    is_automatic=True,
                    opening_stock=None,
                    stock_added=restocked,
                    stock_deducted=dispatched,
                    current_stock=variant.available_quantity,
                    last_updated=shopify_last_updated.get(variant.id),
                )
            )

            for platform in InventoryPlatform.ALL:
                closing_row = platform_closing[platform].get(variant.id)
                opening_row = platform_opening[platform].get(variant.id)
                added, deducted = platform_day_totals[platform].get(variant.id, (0, 0))
                rows.append(
                    PlatformStockSummaryRow(
                        platform=platform,
                        platform_label=InventoryPlatform.LABELS[platform],
                        is_automatic=False,
                        opening_stock=opening_row.quantity_after if opening_row else 0,
                        stock_added=added,
                        stock_deducted=deducted,
                        current_stock=closing_row.quantity_after if closing_row else 0,
                        last_updated=closing_row.created_at if closing_row else None,
                    )
                )

            variant_responses.append(
                VariantPlatformStockResponse(
                    product_variant_id=variant.id,
                    sku=variant.sku,
                    variant_title=variant.title_override or variant.title,
                    stock_date=stock_date,
                    platforms=rows,
                )
            )

        return ProductPlatformStockResponse(
            product_id=product.id,
            product_title=product.title_override or product.title,
            stock_date=stock_date,
            variants=variant_responses,
        )

    # ------------------------------------------------------------------
    # Read: merged movement history (this variant's manual platform
    # movements interleaved with its existing Shopify movements)
    # ------------------------------------------------------------------

    async def get_movement_history(
        self,
        variant_id: uuid.UUID,
        *,
        platform: str | None,
        date_from: date_type | None,
        date_to: date_type | None,
        page_params: PageParams,
    ) -> tuple[list[UnifiedStockMovementResponse], int]:
        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        platform_movements = await self.movements.list_for_date_range(
            product_variant_id=variant_id, platform=platform, date_from=date_from, date_to=date_to
        )
        rows = [self._to_unified_platform_row(m) for m in platform_movements]

        # Shopify is included in the unified view unless the caller
        # explicitly filtered to one manual platform -- the existing,
        # separate Shopify movement-history endpoint/table is completely
        # untouched; this is only an additional merged read.
        if platform is None or platform == "shopify":
            created_from = ist_day_bounds_for_date(date_from)[0] if date_from else None
            created_to = ist_day_bounds_for_date(date_to)[1] if date_to else None
            shopify_query = self.inventory_movements.search_query(
                product_variant_id=variant_id, date_from=created_from, date_to=created_to
            )
            result = await self.session.execute(shopify_query)
            shopify_movements = result.scalars().all()
            rows.extend(self._to_unified_shopify_row(m) for m in shopify_movements)

        rows.sort(key=lambda r: r.created_at, reverse=True)
        total = len(rows)
        start = page_params.offset
        end = start + page_params.page_size
        return rows[start:end], total

    def _to_unified_platform_row(
        self, movement: PlatformStockMovement
    ) -> UnifiedStockMovementResponse:
        actor_label = movement.actor.name if movement.actor is not None else "System"
        return UnifiedStockMovementResponse(
            id=movement.id,
            platform=movement.platform,
            platform_label=InventoryPlatform.LABELS.get(movement.platform, movement.platform),
            movement_type=movement.movement_type.value,
            quantity_delta=movement.quantity_delta,
            quantity_after=movement.quantity_after,
            stock_date=movement.stock_date,
            reason=movement.reason,
            actor_label=actor_label,
            created_at=movement.created_at,
        )

    def _to_unified_shopify_row(self, movement: InventoryMovement) -> UnifiedStockMovementResponse:
        if movement.actor is not None:
            actor_label = movement.actor.name
        elif movement.movement_type in (
            InventoryMovementType.DISPATCH,
            InventoryMovementType.RTO_RESTOCK,
        ):
            actor_label = "Shiprocket"
        else:
            actor_label = "System"
        return UnifiedStockMovementResponse(
            id=movement.id,
            platform="shopify",
            platform_label="Shopify",
            movement_type=movement.movement_type.value,
            quantity_delta=movement.quantity_delta,
            quantity_after=movement.quantity_after,
            stock_date=to_ist(movement.created_at).date(),
            reason=movement.reason,
            actor_label=actor_label,
            created_at=movement.created_at,
        )

    # ------------------------------------------------------------------
    # Read: in-transit / out-for-delivery / delivered-today / RTO summary
    # ------------------------------------------------------------------

    async def get_product_shipment_summary(
        self, product_id: uuid.UUID, *, stock_date: date_type
    ) -> ProductShipmentSummaryResponse:
        """Per-variant shipment-status breakdown, derived entirely from
        the EXISTING `InventoryMovement` DISPATCH rows (already link
        `product_variant_id` + `shipment_id`, boxes already computed)
        joined to `Shipment.current_status` -- never a fabricated count,
        and never a second shipment-tracking mechanism. Mirrors
        `ShipmentService.get_summary`'s bucketing exactly: `in_transit` =
        IN_TRANSIT only, `out_for_delivery` = OUT_FOR_DELIVERY only,
        `rto` = RTO_INITIATED + RTO_DELIVERED.

        `in_transit`/`out_for_delivery`/`rto` are LIVE current counts, not
        scoped to `stock_date` -- shipment status has no historical
        day-by-day snapshot in this codebase, so date-filtering them
        would silently show today's numbers under a past-date label
        (Requirement 10 explicitly forbids this). Only `delivered_on_date`
        is genuinely date-scoped, by `Shipment.actual_delivery_date`
        (mirrors `AnalyticsService.get_summary`'s existing convention).
        """
        product, variants = await self.inventory.list_variants_for_product(product_id)
        variant_ids = [v.id for v in variants]
        if not variant_ids:
            return ProductShipmentSummaryResponse(
                product_id=product.id,
                stock_date=stock_date,
                in_transit=0,
                out_for_delivery=0,
                delivered_on_date=0,
                rto=0,
                variants=[],
            )

        status_stmt = (
            select(
                InventoryMovement.product_variant_id,
                Shipment.current_status,
                -InventoryMovement.quantity_delta,
            )
            .join(Shipment, Shipment.id == InventoryMovement.shipment_id)
            .where(
                InventoryMovement.product_variant_id.in_(variant_ids),
                InventoryMovement.movement_type == InventoryMovementType.DISPATCH,
                Shipment.current_status.in_(
                    [
                        ShipmentStatus.IN_TRANSIT,
                        ShipmentStatus.OUT_FOR_DELIVERY,
                        ShipmentStatus.RTO_INITIATED,
                        ShipmentStatus.RTO_DELIVERED,
                    ]
                ),
            )
        )
        status_result = await self.session.execute(status_stmt)
        by_variant_status: dict[uuid.UUID, dict[ShipmentStatus, int]] = {}
        for variant_id, status, boxes in status_result.all():
            by_variant_status.setdefault(variant_id, {})[status] = (
                by_variant_status.get(variant_id, {}).get(status, 0) + int(boxes or 0)
            )

        day_start, day_end = ist_day_bounds_for_date(stock_date)
        delivered_stmt = (
            select(InventoryMovement.product_variant_id, -InventoryMovement.quantity_delta)
            .join(Shipment, Shipment.id == InventoryMovement.shipment_id)
            .where(
                InventoryMovement.product_variant_id.in_(variant_ids),
                InventoryMovement.movement_type == InventoryMovementType.DISPATCH,
                Shipment.actual_delivery_date >= day_start,
                Shipment.actual_delivery_date < day_end,
            )
        )
        delivered_result = await self.session.execute(delivered_stmt)
        delivered_by_variant: dict[uuid.UUID, int] = {}
        for variant_id, boxes in delivered_result.all():
            delivered_by_variant[variant_id] = delivered_by_variant.get(variant_id, 0) + int(
                boxes or 0
            )

        variant_rows: list[ShipmentTransitSummaryRow] = []
        for variant in variants:
            statuses = by_variant_status.get(variant.id, {})
            variant_rows.append(
                ShipmentTransitSummaryRow(
                    product_variant_id=variant.id,
                    sku=variant.sku,
                    in_transit=statuses.get(ShipmentStatus.IN_TRANSIT, 0),
                    out_for_delivery=statuses.get(ShipmentStatus.OUT_FOR_DELIVERY, 0),
                    delivered_on_date=delivered_by_variant.get(variant.id, 0),
                    rto=statuses.get(ShipmentStatus.RTO_INITIATED, 0)
                    + statuses.get(ShipmentStatus.RTO_DELIVERED, 0),
                )
            )

        return ProductShipmentSummaryResponse(
            product_id=product.id,
            stock_date=stock_date,
            in_transit=sum(r.in_transit for r in variant_rows),
            out_for_delivery=sum(r.out_for_delivery for r in variant_rows),
            delivered_on_date=sum(r.delivered_on_date for r in variant_rows),
            rto=sum(r.rto for r in variant_rows),
            variants=variant_rows,
        )


def to_movement_response(movement: PlatformStockMovement) -> PlatformStockMovementResponse:
    actor_label = movement.actor.name if movement.actor is not None else "System"
    return PlatformStockMovementResponse(
        id=movement.id,
        product_variant_id=movement.product_variant_id,
        platform=movement.platform,
        platform_label=InventoryPlatform.LABELS.get(movement.platform, movement.platform),
        movement_type=movement.movement_type,
        quantity_delta=movement.quantity_delta,
        quantity_after=movement.quantity_after,
        stock_date=movement.stock_date,
        reason=movement.reason,
        actor_user_id=movement.actor_user_id,
        actor_label=actor_label,
        created_at=movement.created_at,
    )
