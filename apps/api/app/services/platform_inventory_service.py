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
from sqlalchemy.exc import IntegrityError
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
    VariantMarketplaceStockResponse,
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

    async def _resolve_marketplace_scope(
        self, product_id: uuid.UUID, catalog_variant_id: uuid.UUID | None
    ) -> tuple[uuid.UUID | None, list]:
        """`(scope catalog_variant_id, the variants whose pack_size/
        packets_per_box decide the conversion)`.

        A product with TWO OR MORE real CatalogVariants (Herbal Masala) is
        tracked PER OMS-visible variant: the caller must name one (never
        defaulted, never guessed), and only THAT variant's SKUs decide
        the conversion. Any other product is product-scoped and must NOT
        name a variant. An underlying 60/120/180 SKU is never a scope.
        """
        _product, variants = await self.inventory.list_variants_for_product(product_id)
        cvs = await self.inventory.catalog_variants.list_for_product(product_id)
        if len(cvs) >= 2:
            if catalog_variant_id is None:
                raise ValidationError(
                    "This product's marketplace stock is tracked per variant -- choose which "
                    "variant (e.g. Gold Packet) this movement belongs to."
                )
            if catalog_variant_id not in {cv.id for cv in cvs}:
                raise ValidationError("That variant does not belong to this product.")
            members = [v for v in variants if v.catalog_variant_id == catalog_variant_id]
            return catalog_variant_id, members
        if catalog_variant_id is not None:
            raise ValidationError(
                "This product's marketplace stock is not tracked per variant; "
                "do not send a variant."
            )
        return None, variants

    async def _latest_balance(
        self,
        *,
        product_id: uuid.UUID,
        catalog_variant_id: uuid.UUID | None,
        platform: str,
        as_of: date_type,
    ) -> int:
        latest = await self.product_movements.get_latest_as_of(
            product_id=product_id,
            catalog_variant_id=catalog_variant_id,
            platform=platform,
            as_of=as_of,
        )
        return latest.quantity_after if latest else 0

    async def _append_movement(
        self,
        *,
        product_id: uuid.UUID,
        catalog_variant_id: uuid.UUID | None,
        platform: str,
        movement_type: ProductMarketplaceMovementType,
        quantity_packets: int,
        delta: int,
        previous_quantity: int,
        stock_date: date_type,
        reason: str | None,
        actor,
        reverses_movement_id: uuid.UUID | None = None,
        replaces_movement_id: uuid.UUID | None = None,
    ) -> ProductMarketplaceMovement:
        """Flush-only append of ONE ledger row plus its audit entry --
        never commits (the caller owns the single transaction) and never
        updates an existing row.
        """
        new_quantity = previous_quantity + delta
        movement = await self.product_movements.create(
            product_id=product_id,
            catalog_variant_id=catalog_variant_id,
            platform=platform,
            movement_type=movement_type,
            quantity_packets=quantity_packets,
            quantity_delta=delta,
            quantity_after=new_quantity,
            stock_date=stock_date,
            reason=reason,
            actor_user_id=actor.id if actor else None,
            reverses_movement_id=reverses_movement_id,
            replaces_movement_id=replaces_movement_id,
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
                "stock_date": stock_date.isoformat(),
                "reason": reason,
                "catalog_variant_id": str(catalog_variant_id) if catalog_variant_id else None,
                "reverses_movement_id": str(reverses_movement_id) if reverses_movement_id else None,
                "replaces_movement_id": str(replaces_movement_id) if replaces_movement_id else None,
            },
        )
        return movement

    @staticmethod
    def _movement_label(platform: str, movement_type: ProductMarketplaceMovementType) -> str:
        label = InventoryPlatform.LABELS.get(platform, platform)
        kind = "sale" if movement_type == ProductMarketplaceMovementType.SALE else "RTO"
        return f"{label} {kind}"

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
        catalog_variant_id: uuid.UUID | None = None,
    ) -> ProductMarketplaceMovement:
        """Record Sale / RTO -- staff enters ONLY a packet quantity, never
        a SKU and never the resulting total. `Sale` is a negative delta
        and `RTO` a positive one, recorded as their own distinct
        `movement_type` (never netted before being stored).

        SCOPE: a product with two or more CatalogVariants (Herbal Masala)
        is tracked per OMS-visible variant -- `catalog_variant_id` is
        required and the movement touches THAT variant's balance only. Any
        other product is product-scoped. See `_resolve_marketplace_scope`.

        TWO ledgers change, in ONE transaction: the marketplace
        `ProductMarketplaceMovement` (this scope+platform's balance and
        history) AND the OMS total stock, via
        `InventoryService.apply_marketplace_stock_effect` (the same ledger
        a manual CatalogVariant Edit Stock uses). If either write fails,
        the whole operation rolls back -- neither ledger is left ahead of
        the other.

        The packet quantity is converted to outers via
        `_product_conversion_factor` (the in-scope SKUs' approved
        pack_size/packets_per_box, the exact formula
        `InventoryService.apply_dispatch` already uses) -- raises instead
        of guessing when that conversion isn't deterministic. No
        `ProductVariant` row is read for a stock decision or written to.
        Add Stock is not a marketplace operation.
        """
        if movement_type not in (
            ProductMarketplaceMovementType.SALE,
            ProductMarketplaceMovementType.RTO,
        ):
            raise ValidationError("Only a Sale or an RTO can be recorded for a marketplace.")
        if quantity_packets <= 0:
            raise ValidationError("Quantity must be a positive number.")
        if platform not in InventoryPlatform.ALL:
            allowed = ", ".join(InventoryPlatform.ALL)
            raise ValidationError(f"Unknown platform {platform!r}. Must be one of: {allowed}.")

        scope_cv, scope_variants = await self._resolve_marketplace_scope(
            product_id, catalog_variant_id
        )
        pack_size, packets_per_box = self._product_conversion_factor(scope_variants)
        outers = _ceil_div(quantity_packets * pack_size, packets_per_box)
        resolved_date = stock_date or ist_today()
        previous_quantity = await self._latest_balance(
            product_id=product_id,
            catalog_variant_id=scope_cv,
            platform=platform,
            as_of=resolved_date,
        )

        # Unlike the per-SKU manual ledger (`record_movement`, above),
        # a Sale here is NOT rejected for taking the balance negative --
        # the approved worked example (20 sold with nothing ever added
        # -> -20) explicitly expects that outcome. Staff records
        # marketplace activity as it happens, not necessarily in the
        # order opening stock arrives; a negative balance is a
        # legitimate, visible signal to reconcile, never blocked.
        delta = -outers if movement_type == ProductMarketplaceMovementType.SALE else outers
        cleaned_reason = reason.strip() if reason and reason.strip() else None

        try:
            movement = await self._append_movement(
                product_id=product_id,
                catalog_variant_id=scope_cv,
                platform=platform,
                movement_type=movement_type,
                quantity_packets=quantity_packets,
                delta=delta,
                previous_quantity=previous_quantity,
                stock_date=resolved_date,
                reason=cleaned_reason,
                actor=actor,
            )
            oms_reason = (
                f"{self._movement_label(platform, movement_type)}: {quantity_packets} packets"
            )
            if cleaned_reason:
                oms_reason = f"{oms_reason} - {cleaned_reason}"
            await self.inventory.apply_marketplace_stock_effect(
                product_id=product_id,
                quantity_delta=delta,
                reason=oms_reason,
                actor=actor,
                product_marketplace_movement_id=movement.id,
                catalog_variant_id=scope_cv,
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return movement

    # ------------------------------------------------------------------
    # Edit / Undo -- append-only corrections (never update or delete a row)
    # ------------------------------------------------------------------

    async def _load_correctable(self, movement_id: uuid.UUID) -> ProductMarketplaceMovement:
        original = await self.product_movements.get_by_id(movement_id)
        if original is None:
            raise NotFoundError("Marketplace movement not found.")
        if original.platform not in InventoryPlatform.ALL:
            # Belt-and-braces: `record_product_movement` already refuses to
            # create a platform="shopify" row (`InventoryPlatform.ALL`
            # deliberately excludes it), so this can't happen through the
            # normal API -- but Edit/Undo must never act on a Shopify-
            # synced row even if one existed (e.g. a future data import).
            raise ValidationError(
                "Shopify's stock is automatic and synced; it cannot be edited or undone here."
            )
        if original.movement_type not in (
            ProductMarketplaceMovementType.SALE,
            ProductMarketplaceMovementType.RTO,
        ):
            raise ValidationError(
                "Only a manually recorded Sale or RTO can be edited or undone (a reversal cannot)."
            )
        if await self.product_movements.reversed_ids([original.id]):
            raise ValidationError("This movement has already been undone or edited.")
        return original

    async def _append_reversal(
        self,
        original: ProductMarketplaceMovement,
        *,
        reason: str,
        actor,
        stock_date: date_type,
        previous_quantity: int,
    ) -> ProductMarketplaceMovement:
        """Flush-only. The reversal's marketplace delta is the exact
        negative of the original's; its OMS-total effect is the exact
        negative of what the original ACTUALLY wrote to the OMS ledger --
        zero if it wrote none (a movement recorded before OMS-stock
        linking), so undoing a legacy row can never double-count.
        """
        reversal = await self._append_movement(
            product_id=original.product_id,
            catalog_variant_id=original.catalog_variant_id,
            platform=original.platform,
            movement_type=ProductMarketplaceMovementType.REVERSAL,
            quantity_packets=original.quantity_packets,
            delta=-original.quantity_delta,
            previous_quantity=previous_quantity,
            stock_date=stock_date,
            reason=reason,
            actor=actor,
            reverses_movement_id=original.id,
        )
        original_effect = await self.inventory.catalog_variant_adjustments.get_for_movement(
            original.id
        )
        if original_effect is not None:
            label = self._movement_label(original.platform, original.movement_type)
            await self.inventory.apply_marketplace_stock_effect(
                product_id=original.product_id,
                quantity_delta=-original_effect.quantity_delta,
                reason=f"{label} reversed: {original.quantity_packets} packets - {reason}",
                actor=actor,
                product_marketplace_movement_id=reversal.id,
                catalog_variant_id=original_effect.catalog_variant_id,
                product_level=original_effect.catalog_variant_id is None,
            )
        return reversal

    async def undo_movement(
        self, movement_id: uuid.UUID, *, reason: str, actor
    ) -> ProductMarketplaceMovement:
        """Undo a manual Sale/RTO by APPENDING a reversal row -- the
        original is never deleted or changed, so the history keeps both
        ("Sale -20" then "Reversal +20") and the effective balance returns
        to its pre-movement value. Dated today: past days' balances were
        already true when they were recorded and are never restated.
        """
        cleaned = (reason or "").strip()
        if not cleaned:
            raise ValidationError("A reason is required.")
        original = await self._load_correctable(movement_id)
        today = ist_today()
        try:
            previous = await self._latest_balance(
                product_id=original.product_id,
                catalog_variant_id=original.catalog_variant_id,
                platform=original.platform,
                as_of=today,
            )
            reversal = await self._append_reversal(
                original, reason=cleaned, actor=actor, stock_date=today, previous_quantity=previous
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValidationError("This movement has already been undone or edited.") from exc
        except Exception:
            await self.session.rollback()
            raise
        return reversal

    async def edit_movement(
        self, movement_id: uuid.UUID, *, quantity_packets: int, reason: str, actor
    ) -> ProductMarketplaceMovement:
        """Correct a manual Sale/RTO's quantity WITHOUT touching the
        original: in one transaction, append a reversal of it and a
        replacement (same type/platform/scope) at the new quantity. The
        history keeps "Sale -20", "Reversal +20", "Sale -15"; the
        effective result is -15. The replacement's OMS effect is a normal
        one for the NEW quantity, so editing a legacy row (recorded
        before OMS-stock linking) also brings the OMS total in line.
        """
        cleaned = (reason or "").strip()
        if not cleaned:
            raise ValidationError("A reason is required.")
        if quantity_packets <= 0:
            raise ValidationError("Quantity must be a positive number.")
        original = await self._load_correctable(movement_id)
        if quantity_packets == original.quantity_packets:
            raise ValidationError("The new quantity must be different from the current one.")

        # Conversion for the NEW quantity, in the original's own scope.
        # Checked before anything is written so a non-deterministic
        # conversion rejects cleanly (422) with both ledgers untouched.
        _product, variants = await self.inventory.list_variants_for_product(original.product_id)
        scope_variants = (
            [v for v in variants if v.catalog_variant_id == original.catalog_variant_id]
            if original.catalog_variant_id is not None
            else variants
        )
        pack_size, packets_per_box = self._product_conversion_factor(scope_variants)
        outers = _ceil_div(quantity_packets * pack_size, packets_per_box)
        delta = -outers if original.movement_type == ProductMarketplaceMovementType.SALE else outers

        today = ist_today()
        try:
            previous = await self._latest_balance(
                product_id=original.product_id,
                catalog_variant_id=original.catalog_variant_id,
                platform=original.platform,
                as_of=today,
            )
            reversal = await self._append_reversal(
                original, reason=cleaned, actor=actor, stock_date=today, previous_quantity=previous
            )
            replacement = await self._append_movement(
                product_id=original.product_id,
                catalog_variant_id=original.catalog_variant_id,
                platform=original.platform,
                movement_type=original.movement_type,
                quantity_packets=quantity_packets,
                delta=delta,
                previous_quantity=previous + reversal.quantity_delta,
                stock_date=today,
                reason=cleaned,
                actor=actor,
                replaces_movement_id=original.id,
            )
            label = self._movement_label(original.platform, original.movement_type)
            await self.inventory.apply_marketplace_stock_effect(
                product_id=original.product_id,
                quantity_delta=delta,
                reason=(
                    f"{label} (edited {original.quantity_packets} -> {quantity_packets}): "
                    f"{quantity_packets} packets - {cleaned}"
                ),
                actor=actor,
                product_marketplace_movement_id=replacement.id,
                catalog_variant_id=original.catalog_variant_id,
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValidationError("This movement has already been undone or edited.") from exc
        except Exception:
            await self.session.rollback()
            raise
        return replacement

    async def describe_movements(
        self, movements: list[ProductMarketplaceMovement]
    ) -> list[ProductMarketplaceMovementResponse]:
        """Response rows with their EFFECTIVE state, derived (never
        stored) from the reversal/replacement links -- one bulk lookup,
        no N+1: `active`, `undone` (reversed, not replaced), `edited`
        (reversed and replaced), or `reversal`. Only an `active` Sale/RTO
        can be edited or undone.
        """
        ids = [m.id for m in movements]
        reversed_set = await self.product_movements.reversed_ids(ids)
        replaced_set = await self.product_movements.replaced_ids(ids)
        replaced_origins = [m.replaces_movement_id for m in movements if m.replaces_movement_id]
        original_packets = await self.product_movements.packets_by_id(replaced_origins)

        described: list[ProductMarketplaceMovementResponse] = []
        for m in movements:
            if m.movement_type == ProductMarketplaceMovementType.REVERSAL:
                status = "reversal"
            elif m.id in reversed_set:
                status = "edited" if m.id in replaced_set else "undone"
            else:
                status = "active"
            correctable = status == "active" and m.movement_type in (
                ProductMarketplaceMovementType.SALE,
                ProductMarketplaceMovementType.RTO,
            )
            described.append(
                to_product_movement_response(
                    m,
                    status=status,
                    can_edit=correctable,
                    can_undo=correctable,
                    edited_from_packets=(
                        original_packets.get(m.replaces_movement_id)
                        if m.replaces_movement_id
                        else None
                    ),
                )
            )
        return described

    # ------------------------------------------------------------------
    # Read: platform stock summary for a date
    # ------------------------------------------------------------------

    async def _manual_platform_rows(
        self,
        *,
        product_id: uuid.UUID,
        catalog_variant_id: uuid.UUID | None,
        stock_date: date_type,
    ) -> list[PlatformStockSummaryRow]:
        """Every manual platform's row for ONE scope, straight from
        `ProductMarketplaceMovement` (already scoped -- no SKU-level
        summation to do at all).
        """
        opening_date = stock_date - timedelta(days=1)
        rows: list[PlatformStockSummaryRow] = []
        for platform in InventoryPlatform.ALL:
            closing_row = await self.product_movements.get_latest_as_of(
                product_id=product_id,
                catalog_variant_id=catalog_variant_id,
                platform=platform,
                as_of=stock_date,
            )
            opening_row = await self.product_movements.get_latest_as_of(
                product_id=product_id,
                catalog_variant_id=catalog_variant_id,
                platform=platform,
                as_of=opening_date,
            )
            added, deducted = await self.product_movements.sum_for_date(
                product_id=product_id,
                catalog_variant_id=catalog_variant_id,
                platform=platform,
                stock_date=stock_date,
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
        return rows

    async def _sold_this_month(
        self, *, product_id: uuid.UUID, catalog_variant_id: uuid.UUID | None
    ) -> int:
        """ "Sold This Month": the CURRENT IST calendar month (independent
        of the selected `stock_date`), effective SALE movements only,
        summed from the packet quantities staff actually entered.
        """
        month_start = ist_today().replace(day=1)
        next_month_start = (
            month_start.replace(year=month_start.year + 1, month=1)
            if month_start.month == 12
            else month_start.replace(month=month_start.month + 1)
        )
        return await self.product_movements.sum_sold_packets(
            product_id=product_id,
            catalog_variant_id=catalog_variant_id,
            date_from=month_start,
            date_to_exclusive=next_month_start,
        )

    async def get_product_platform_stock(
        self, product_id: uuid.UUID, *, stock_date: date_type
    ) -> ProductPlatformStockResponse:
        product, variants = await self.inventory.list_variants_for_product(product_id)
        cvs = await self.inventory.catalog_variants.list_for_product(product_id)
        variant_ids = [v.id for v in variants]
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

        def shopify_row(scope_variants: list) -> PlatformStockSummaryRow:
            """ONE Shopify row summed across `scope_variants` (the whole
            product, or one OMS-visible variant's own SKUs).
            `opening_stock`/`current_stock` propagate `None` (never
            silently sum only the known SKUs) if ANY contributing SKU's
            value is `None` for this date -- a partial sum that looks
            complete would misrepresent Shopify's historical balance as
            known when it genuinely isn't for at least one SKU.
            """
            added_total = 0
            deducted_total = 0
            opening: int | None = 0
            current: int | None = 0
            last_updated_at = None
            for variant in scope_variants:
                added, deducted = shopify_totals.get(variant.id, (0, 0))
                added_total += added
                deducted_total += deducted

                opening_movement = shopify_opening_as_of.get(variant.id)
                variant_opening = opening_movement.quantity_after if opening_movement else None
                opening = (
                    None
                    if opening is None or variant_opening is None
                    else opening + variant_opening
                )

                if is_today:
                    variant_current: int | None = variant.available_quantity
                else:
                    closing_movement = shopify_closing_as_of.get(variant.id)
                    # `None` (never 0) when no movement exists before this
                    # cutoff -- the ledger genuinely cannot reconstruct
                    # this date's balance; never guessed.
                    variant_current = closing_movement.quantity_after if closing_movement else None
                current = (
                    None
                    if current is None or variant_current is None
                    else current + variant_current
                )

                variant_last_updated = shopify_last_updated.get(variant.id)
                if variant_last_updated and (
                    last_updated_at is None or variant_last_updated > last_updated_at
                ):
                    last_updated_at = variant_last_updated

            return PlatformStockSummaryRow(
                platform="shopify",
                platform_label="Shopify",
                is_automatic=True,
                opening_stock=opening,
                stock_added=added_total,
                stock_deducted=deducted_total,
                current_stock=current,
                last_updated=last_updated_at,
            )

        title = product.title_override or product.title

        # TWO OR MORE CatalogVariants (Herbal Masala): one independent
        # Marketplace Stock table per OMS-visible variant. The 60/120/180
        # SKUs only feed their own variant's Shopify row.
        if len(cvs) >= 2:
            sections: list[VariantMarketplaceStockResponse] = []
            for cv in cvs:
                members = [v for v in variants if v.catalog_variant_id == cv.id]
                sections.append(
                    VariantMarketplaceStockResponse(
                        catalog_variant_id=cv.id,
                        name=cv.name,
                        display_order=cv.display_order,
                        platforms=[
                            shopify_row(members),
                            *await self._manual_platform_rows(
                                product_id=product_id,
                                catalog_variant_id=cv.id,
                                stock_date=stock_date,
                            ),
                        ],
                        sold_this_month_packets=await self._sold_this_month(
                            product_id=product_id, catalog_variant_id=cv.id
                        ),
                    )
                )
            return ProductPlatformStockResponse(
                product_id=product.id,
                product_title=title,
                stock_date=stock_date,
                scope="catalog_variant",
                platforms=[],
                variants=sections,
                sold_this_month_packets=0,
            )

        return ProductPlatformStockResponse(
            product_id=product.id,
            product_title=title,
            stock_date=stock_date,
            scope="product",
            platforms=[
                shopify_row(variants),
                *await self._manual_platform_rows(
                    product_id=product_id, catalog_variant_id=None, stock_date=stock_date
                ),
            ],
            sold_this_month_packets=await self._sold_this_month(
                product_id=product_id, catalog_variant_id=None
            ),
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
        catalog_variant_id: uuid.UUID | None = None,
    ) -> tuple[list[ProductMarketplaceMovementResponse], int]:
        """Marketplace history -- Sale / RTO / Reversal, each its own
        event (a Sale, its reversal and a replacement all appear as
        separate rows, never merged or netted). `catalog_variant_id`
        filters to ONE OMS-visible variant's own history (Gold's history
        never includes Red's). Distinct from the per-SKU
        `get_movement_history` above; neither reads the other's table.
        """
        # Only used to raise NotFoundError when the product doesn't exist.
        await self.inventory.list_variants_for_product(product_id)
        items, total = await self.product_movements.list(
            page_params=page_params,
            query=self.product_movements.search_query(
                product_id=product_id,
                platform=platform,
                date_from=date_from,
                date_to=date_to,
                catalog_variant_id=catalog_variant_id,
            ),
            default_sort_column="created_at",
        )
        return await self.describe_movements(list(items)), total


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
    *,
    status: str = "active",
    can_edit: bool = False,
    can_undo: bool = False,
    edited_from_packets: int | None = None,
) -> ProductMarketplaceMovementResponse:
    actor_label = movement.actor.name if movement.actor is not None else "System"
    return ProductMarketplaceMovementResponse(
        id=movement.id,
        product_id=movement.product_id,
        catalog_variant_id=movement.catalog_variant_id,
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
        reverses_movement_id=movement.reverses_movement_id,
        replaces_movement_id=movement.replaces_movement_id,
        edited_from_packets=edited_from_packets,
        status=status,
        can_edit=can_edit,
        can_undo=can_undo,
    )
