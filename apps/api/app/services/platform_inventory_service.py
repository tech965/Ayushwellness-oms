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
from datetime import UTC, datetime, timedelta
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.timezone import ist_day_bounds_for_date, ist_today, to_ist
from app.models.enums import (
    InventoryMovementType,
    PlatformStockMovementType,
    ProductMarketplaceMovementType,
    ShipmentStatus,
)
from app.models.inventory import InventoryMovement
from app.models.platform_inventory import (
    InventoryPlatform,
    PlatformStockMovement,
    ProductMarketplaceMovement,
)
from app.models.shipment import Shipment
from app.repositories.inventory import InventoryMovementRepository
from app.repositories.platform_inventory import (
    PlatformStockMovementRepository,
    ProductMarketplaceMovementRepository,
)
from app.repositories.product import ProductVariantRepository
from app.schemas.common import PageParams
from app.schemas.platform_inventory import (
    PlatformStockMovementResponse,
    PlatformStockSummaryRow,
    ProductMarketplaceMovementResponse,
    ProductPlatformStockResponse,
    ProductShipmentSummaryResponse,
    ShipmentTransitSummaryRow,
    UnifiedStockMovementResponse,
)
from app.services.audit_service import AuditService
from app.services.inventory_service import InventoryService, _ceil_div


class PlatformInventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.movements = PlatformStockMovementRepository(session)
        self.product_movements = ProductMarketplaceMovementRepository(session)
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
    # Write: record ONE product-level manual marketplace movement
    # (Add Stock / Record Sale / RTO) -- no SKU is selected or implied.
    # ------------------------------------------------------------------

    @staticmethod
    def _product_conversion_factor(variants: list) -> tuple[int, int]:
        """`(pack_size, packets_per_box)` -- the ONE conversion ratio
        that safely turns a bare "N packets" entry into outers for this
        product as a whole. Only returns a value when every real
        `ProductVariant` under the product agrees on BOTH factors (the
        exact same "uniform" check `packets_per_box_uniform` already
        applies elsewhere in this codebase -- see
        `app.api.v1.endpoints.inventory._product_stock_response` --
        extended here to `pack_size` too, since `InventoryService.
        apply_dispatch`'s approved formula is `ceil(quantity * pack_size
        / packets_per_box)`).

        Raises `ValidationError` (never guesses/averages/picks one SKU)
        when the product has no variants, or when either factor
        disagrees across its SKUs -- e.g. once the approved
        `scripts/backfill_pack_sizes.py` sets the canonical Herbal
        Masala product's pack_size to 1/2/3 per pouch count, this
        product-level ledger correctly REFUSES new writes for it,
        because "20 packets sold" no longer has one deterministic
        outer-count without knowing which pack size was sold.
        """
        if not variants:
            raise ValidationError(
                "This product has no variants to record marketplace stock against."
            )

        pack_sizes = {v.pack_size for v in variants}
        packets_per_box_values = {v.packets_per_box for v in variants}
        if len(pack_sizes) > 1 or len(packets_per_box_values) > 1:
            raise ValidationError(
                "This product's SKUs use different pack sizes, so a single product-level "
                "packet quantity cannot be converted to a deterministic stock change. "
                "Record this movement per SKU instead, or standardize the product's pack "
                "sizes first."
            )
        return next(iter(pack_sizes)), next(iter(packets_per_box_values))

    async def record_product_movement(
        self,
        product_id: uuid.UUID,
        *,
        platform: str,
        movement_type: ProductMarketplaceMovementType,
        quantity_packets: int,
        reason: str | None,
        stock_date: date_type | None,
        actor,
    ) -> ProductMarketplaceMovement:
        """Product-level Add Stock / Record Sale / RTO -- staff enters
        ONLY a packet quantity for the whole product on this platform,
        never a SKU and never the resulting total. `Sale` is a negative
        delta, `Add Stock`/`RTO` are positive -- recorded as their own
        distinct `movement_type` so history never merges an RTO into a
        generic "stock added" bucket (Record Sale = -qty, RTO/Returned
        = +qty are separate events, never netted before being stored).

        The packet quantity is converted to outers via
        `_product_conversion_factor` (this product's own approved
        pack_size/packets_per_box, the exact formula
        `InventoryService.apply_dispatch` already uses) -- raises
        instead of guessing when that conversion isn't deterministic.
        No `ProductVariant` row is read for a stock decision or written
        to by this method.
        """
        if quantity_packets <= 0:
            raise ValidationError("Quantity must be a positive number.")
        if platform not in InventoryPlatform.ALL:
            allowed = ", ".join(InventoryPlatform.ALL)
            raise ValidationError(f"Unknown platform {platform!r}. Must be one of: {allowed}.")

        _product, variants = await self.inventory.list_variants_for_product(product_id)
        pack_size, packets_per_box = self._product_conversion_factor(variants)
        outers = _ceil_div(quantity_packets * pack_size, packets_per_box)

        resolved_date = stock_date or ist_today()

        latest = await self.product_movements.get_latest_as_of(
            product_id=product_id, platform=platform, as_of=resolved_date
        )
        previous_quantity = latest.quantity_after if latest else 0

        # Unlike the per-SKU manual ledger (`record_movement`, above),
        # a Sale here is NOT rejected for taking the balance negative --
        # the approved worked example (20 sold with nothing ever added
        # -> -20) explicitly expects that outcome. Staff records
        # marketplace activity as it happens, not necessarily in the
        # order opening stock arrives; a negative product-level balance
        # is a legitimate, visible signal to reconcile, never blocked.
        delta = -outers if movement_type == ProductMarketplaceMovementType.SALE else outers

        new_quantity = previous_quantity + delta
        cleaned_reason = reason.strip() if reason and reason.strip() else None

        movement = await self.product_movements.create(
            product_id=product_id,
            platform=platform,
            movement_type=movement_type,
            quantity_packets=quantity_packets,
            quantity_delta=delta,
            quantity_after=new_quantity,
            stock_date=resolved_date,
            reason=cleaned_reason,
            actor_user_id=actor.id if actor else None,
            # Explicit Python-clock timestamp (microsecond precision),
            # not the column's server_default -- `get_latest_as_of`
            # breaks ties on `created_at.desc()` among same-`stock_date`
            # rows, and SQLite's `CURRENT_TIMESTAMP` only has SECOND
            # precision, which makes two movements recorded within the
            # same second on the same platform/day genuinely ambiguous
            # (falls through to `id.desc()`, a random UUID -- no
            # relation to actual write order). Postgres's `now()` is
            # already microsecond-precise, so this only changes
            # behavior for the SQLite test environment, making it match
            # real production ordering instead of racing it.
            created_at=datetime.now(UTC),
        )
        await self.audit.record(
            user=actor,
            action="inventory.product_marketplace_movement",
            entity_type="product",
            entity_id=str(product_id),
            previous_value={"platform": platform, "outers": previous_quantity},
            new_value={"platform": platform, "outers": new_quantity},
            metadata={
                "movement_type": movement_type.value,
                "quantity_packets": quantity_packets,
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
        is_today = stock_date == ist_today()

        day_start, day_end = ist_day_bounds_for_date(stock_date)
        shopify_totals = await self.inventory_movements.movement_totals_by_sign_bulk(
            product_variant_ids=variant_ids, created_from=day_start, created_to=day_end
        )
        shopify_last_updated = await self.inventory_movements.last_movement_at_bulk(
            product_variant_ids=variant_ids, created_to=day_end
        )
        # Historical Shopify balance reconstruction (Requirement: never
        # `ProductVariant.available_quantity` for a past date -- that
        # column is only ever today's LIVE value). `quantity_after` on
        # the latest movement at/before a cutoff IS the balance at that
        # instant -- skipped entirely for `is_today` below, where the
        # true live column is used instead (always exact, even if some
        # non-ledger process ever touched it).
        shopify_closing_as_of: dict[uuid.UUID, InventoryMovement] = {}
        if not is_today:
            shopify_closing_as_of = await self.inventory_movements.get_latest_as_of_bulk(
                product_variant_ids=variant_ids, created_to=day_end
            )
        shopify_opening_as_of = await self.inventory_movements.get_latest_as_of_bulk(
            product_variant_ids=variant_ids, created_to=day_start
        )

        # Shopify: ONE row for the whole product, summed across every
        # real underlying SKU. `opening_stock`/`current_stock` propagate
        # `None` (never silently sum only the known SKUs) if ANY
        # contributing SKU's value is `None` for this date -- a partial
        # sum that looks complete would misrepresent Shopify's
        # historical balance as known when it genuinely isn't for at
        # least one SKU (was previously done client-side in
        # `aggregatePlatformRows`, moved here so both the API response
        # and any other future consumer share one source of truth).
        shopify_added = 0
        shopify_deducted = 0
        shopify_opening: int | None = 0
        shopify_current: int | None = 0
        shopify_last_updated_at = None
        for variant in variants:
            added, deducted = shopify_totals.get(variant.id, (0, 0))
            shopify_added += added
            shopify_deducted += deducted

            opening_movement = shopify_opening_as_of.get(variant.id)
            variant_opening = opening_movement.quantity_after if opening_movement else None
            shopify_opening = (
                None
                if shopify_opening is None or variant_opening is None
                else shopify_opening + variant_opening
            )

            if is_today:
                variant_current: int | None = variant.available_quantity
            else:
                closing_movement = shopify_closing_as_of.get(variant.id)
                # `None` (never 0) when no movement exists before this
                # cutoff -- the ledger genuinely cannot reconstruct this
                # date's balance (it may predate the variant's
                # first-ever movement); never guessed.
                variant_current = closing_movement.quantity_after if closing_movement else None
            shopify_current = (
                None
                if shopify_current is None or variant_current is None
                else shopify_current + variant_current
            )

            variant_last_updated = shopify_last_updated.get(variant.id)
            if variant_last_updated and (
                shopify_last_updated_at is None or variant_last_updated > shopify_last_updated_at
            ):
                shopify_last_updated_at = variant_last_updated

        rows: list[PlatformStockSummaryRow] = [
            PlatformStockSummaryRow(
                platform="shopify",
                platform_label="Shopify",
                is_automatic=True,
                opening_stock=shopify_opening,
                stock_added=shopify_added,
                stock_deducted=shopify_deducted,
                current_stock=shopify_current,
                last_updated=shopify_last_updated_at,
            )
        ]

        # Every manual platform: ONE row for the whole product, sourced
        # directly from `ProductMarketplaceMovement` -- already
        # product-scoped, so unlike Shopify above there is no per-SKU
        # summation to do at all.
        for platform in InventoryPlatform.ALL:
            closing_row = await self.product_movements.get_latest_as_of(
                product_id=product_id, platform=platform, as_of=stock_date
            )
            opening_row = await self.product_movements.get_latest_as_of(
                product_id=product_id, platform=platform, as_of=opening_date
            )
            added, deducted = await self.product_movements.sum_for_date(
                product_id=product_id, platform=platform, stock_date=stock_date
            )
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

        return ProductPlatformStockResponse(
            product_id=product.id,
            product_title=product.title_override or product.title,
            stock_date=stock_date,
            platforms=rows,
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
            by_variant_status.setdefault(variant_id, {})[status] = by_variant_status.get(
                variant_id, {}
            ).get(status, 0) + int(boxes or 0)

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

    # ------------------------------------------------------------------
    # Read: product-level marketplace adjustment history
    # ------------------------------------------------------------------

    async def get_product_marketplace_history(
        self,
        product_id: uuid.UUID,
        *,
        platform: str | None,
        date_from: date_type | None,
        date_to: date_type | None,
        page_params: PageParams,
    ) -> tuple[list[ProductMarketplaceMovementResponse], int]:
        """Product-level "Add Stock" / "Sale" / "RTO" history -- each is
        its own event, never merged or netted (a sale and a later RTO
        for the same platform/day both appear as separate rows). This
        is a distinct, additional view from the existing per-SKU
        `get_movement_history` above; neither reads or writes the
        other's table.
        """
        # Only used to raise NotFoundError when the product doesn't exist.
        await self.inventory.list_variants_for_product(product_id)
        items, total = await self.product_movements.list(
            page_params=page_params,
            query=self.product_movements.search_query(
                product_id=product_id, platform=platform, date_from=date_from, date_to=date_to
            ),
            default_sort_column="created_at",
        )
        return [to_product_movement_response(m) for m in items], total


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


def to_product_movement_response(
    movement: ProductMarketplaceMovement,
) -> ProductMarketplaceMovementResponse:
    actor_label = movement.actor.name if movement.actor is not None else "System"
    return ProductMarketplaceMovementResponse(
        id=movement.id,
        product_id=movement.product_id,
        platform=movement.platform,
        platform_label=InventoryPlatform.LABELS.get(movement.platform, movement.platform),
        movement_type=movement.movement_type,
        quantity_packets=movement.quantity_packets,
        quantity_delta=movement.quantity_delta,
        quantity_after=movement.quantity_after,
        stock_date=movement.stock_date,
        reason=movement.reason,
        actor_user_id=movement.actor_user_id,
        actor_label=actor_label,
        created_at=movement.created_at,
    )
