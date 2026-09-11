"""OMS-authoritative stock tracking, in BOXES.

Inventory is owned and controlled entirely by the OMS. The only external
signal that ever moves `ProductVariant.available_quantity` (boxes) is
Shiprocket shipment/RTO status, via `apply_dispatch`/`apply_rto_restock`
below (called from `app.integrations.shiprocket.sync.apply_tracking_event`
and `app.services.rto_service.RTOService.update_rto`). Shopify's own
`inventory_quantity` is a passive reference field only -- it is NEVER
read by any calculation in this module, never seeds/initializes OMS
stock (a newly-created `ProductVariant` starts at its column default, 0
boxes -- see `ProductService.upsert_synced_product`), and this module
never writes back to Shopify. From creation on, `available_quantity` is
only ever moved from here: down on dispatch, up on RTO restock, or by a
staff manual adjustment. Every move writes exactly one `InventoryMovement`
row -- the ledger is the audit trail and, for the two automatic movement
types, also the idempotency guard (see `apply_dispatch`/`apply_rto_restock`).

`OrderItem.quantity` is in UNITS of a specific variant/SKU (Shopify's own
per-line-item quantity, e.g. "1" for one 120-Pack bundle purchased), not
already in packets. Each variant configures two independent, per-variant
ratios (never hardcoded constants): `pack_size` (how many packets/pouches
ONE unit of this variant contains -- 60 for a "60 Pack" SKU, 120 for a
"120 Pack" SKU) and `packets_per_box` (how many packets fit in one
physical warehouse box). A dispatch/restock first converts the order
line to a true packet count (`quantity * pack_size`), then converts
THAT to boxes via ceiling division (`_ceil_div`) against `packets_per_box`
-- a partially-consumed box still consumes one whole box-equivalent of
physical stock. Both ratios default to 1, so a variant with neither
configured behaves exactly as before this two-ratio conversion existed
(1 unit ordered == 1 box).

Idempotency: `OrderItem` has no per-shipment/per-RTO quantity split (it
only ever records the order line's full quantity), so a dispatch/restock
is necessarily an order-scoped event applied once per (order, variant,
movement type) -- this is the correct behaviour for this data model, not
a simplification, since there is no way to know which of an order's
items went into which of the order's shipments if it ever has more than
one (the schema does not forbid that -- see `Shipment.order_id`/
`RTO.order_id`, plain non-unique foreign keys). The `exists_for_order`
check alone is only safe against sequential re-fires (a shipment
advancing through several statuses, a pull-sync re-scan); it is NOT
sufficient against two concurrent transactions racing the same check.
`inventory_movements` therefore also carries a DB-level
`UniqueConstraint("product_variant_id", "order_id", "movement_type")`
(see `app.models.inventory.InventoryMovement`) as the actual safety net
-- `apply_dispatch`/`apply_rto_restock` wrap their write in a SAVEPOINT
(`session.begin_nested()`) and treat the resulting `IntegrityError` as
"another transaction already recorded this movement," never as a
failure. Manual adjustments/initial-stock rows always have `order_id
IS NULL`, and NULL never collides with itself in a SQL unique
constraint, so this never restricts them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import select  # pyright: ignore[reportMissingImports]
from sqlalchemy.exc import IntegrityError  # pyright: ignore[reportMissingImports]

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.auth import User
from app.models.enums import InventoryMovementType, StockStatus
from app.models.inventory import InventoryMovement
from app.models.product import (
    CatalogVariant,
    CatalogVariantStockAdjustment,
    Product,
    ProductVariant,
)
from app.models.settings import AppSettings
from app.repositories.inventory import (
    CatalogVariantStockAdjustmentRepository,
    InventoryMovementRepository,
    InventoryProductRepository,
    InventoryStockRepository,
)
from app.repositories.order import OrderItemRepository
from app.repositories.product import CatalogVariantRepository, ProductVariantRepository
from app.schemas.common import PageParams, SortParams
from app.schemas.settings import AppSettingsData
from app.services.audit_service import AuditService

logger = get_logger(__name__)


class OmsVariantGroup(NamedTuple):
    """One OMS-visible variant: either a real `CatalogVariant`
    (`catalog_variant_id` set) grouping >=1 underlying Shopify
    `ProductVariant` rows, or -- for a `ProductVariant` not yet grouped --
    an implicit single-member group (`catalog_variant_id` None). Never
    holds a synthetic/fabricated stock number; callers aggregate
    `variants[*].available_quantity` themselves.
    """

    catalog_variant_id: uuid.UUID | None
    name: str
    display_order: int
    is_active: bool
    variants: list[ProductVariant]


def _ceil_div(numerator: int, denominator: int) -> int:
    """Exact integer ceiling division -- 130 packets / 60 per box = 3
    boxes (not 2.166..., and not truncated to 2).
    """
    return -(-numerator // denominator)


class InventoryService:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.products = InventoryProductRepository(session)
        self.stock = InventoryStockRepository(session)
        self.variants = ProductVariantRepository(session)
        self.catalog_variants = CatalogVariantRepository(session)
        self.catalog_variant_adjustments = CatalogVariantStockAdjustmentRepository(session)
        self.movements = InventoryMovementRepository(session)
        self.order_items = OrderItemRepository(session)
        self.audit = AuditService(session)

    # --- reads ----------------------------------------------------------

    async def get_low_stock_threshold(self) -> int:
        row = (await self.session.execute(select(AppSettings).limit(1))).scalar_one_or_none()
        values = row.values if row is not None else {}
        return AppSettingsData.model_validate(values or {}).inventory.low_stock_threshold

    @staticmethod
    def compute_stock_status(available_boxes: int, threshold: int) -> StockStatus:
        if available_boxes <= 0:
            return StockStatus.OUT_OF_STOCK
        if available_boxes <= threshold:
            return StockStatus.LOW_STOCK
        return StockStatus.IN_STOCK

    async def list_products(
        self, *, page_params: PageParams, sort_params: SortParams, q: str | None = None
    ) -> tuple[list[Product], int]:
        query = self.products.search_query(q=q)
        items, total = await self.products.list(
            page_params=page_params,
            sort_params=sort_params,
            query=query,
            default_sort_column="title",
        )
        return list(items), total

    async def list_variants_for_product(
        self, product_id: uuid.UUID
    ) -> tuple[Product, list[ProductVariant]]:
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found.")
        variants = await self.variants.list_for_product(product_id)
        return product, variants

    async def get_oms_variants_for_product(
        self, product_id: uuid.UUID
    ) -> tuple[Product, list[OmsVariantGroup]]:
        """Resolve a product's OMS-visible variants -- the ONLY variant
        view the Inventory UI shows.

        Each declared `CatalogVariant` becomes one OMS variant grouping
        the underlying `ProductVariant` rows mapped to it (shown even
        when it has zero members yet). Any `ProductVariant` that is not
        mapped to a listed `CatalogVariant` (`catalog_variant_id` NULL,
        or -- defensively -- a dangling id) is surfaced as its own
        implicit single-member OMS variant, so a product that has not
        been grouped yet behaves exactly as before this layer existed.

        Underlying `ProductVariant` rows are never merged, renamed, or
        dropped -- they are only bucketed for display.
        """
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found.")

        variants = await self.variants.list_for_product(product_id)
        catalog = await self.catalog_variants.list_for_product(product_id)

        members: dict[uuid.UUID | None, list[ProductVariant]] = {}
        for variant in variants:
            members.setdefault(variant.catalog_variant_id, []).append(variant)

        groups: list[OmsVariantGroup] = [
            OmsVariantGroup(
                catalog_variant_id=cv.id,
                name=cv.name,
                display_order=cv.display_order,
                is_active=cv.is_active,
                variants=members.pop(cv.id, []),
            )
            for cv in catalog
        ]

        leftover = sorted(
            (v for bucket in members.values() for v in bucket),
            key=lambda v: (v.title_override or v.title or v.sku).lower(),
        )
        for offset, variant in enumerate(leftover):
            groups.append(
                OmsVariantGroup(
                    catalog_variant_id=None,
                    name=variant.title_override or variant.title or variant.sku,
                    display_order=10_000 + offset,
                    is_active=True,
                    variants=[variant],
                )
            )

        groups.sort(key=lambda g: (g.display_order, g.name.lower()))
        return product, groups

    async def set_catalog_variant_name(
        self, catalog_variant_id: uuid.UUID, *, name: str, actor: User | None
    ) -> CatalogVariant:
        """Rename an OMS-visible `CatalogVariant`. Presentation only: no
        `ProductVariant`, stock, SKU, or ledger row is touched. Shopify
        sync never reads or writes this name.
        """
        cv = await self.session.get(CatalogVariant, catalog_variant_id)
        if cv is None:
            raise NotFoundError("Catalog variant not found.")

        normalized = name.strip()
        if not normalized:
            raise ValidationError("Name cannot be empty.")
        previous = cv.name
        if previous == normalized:
            return cv

        await self.catalog_variants.update(cv, name=normalized)
        await self.audit.record(
            user=actor,
            action="inventory.catalog_variant_name_updated",
            entity_type="catalog_variant",
            entity_id=str(cv.id),
            previous_value={"name": previous},
            new_value={"name": normalized},
        )
        await self.session.commit()
        return cv

    async def get_variant_stock(self, variant_id: uuid.UUID) -> ProductVariant:
        variant = await self.stock.get_by_id_with_product(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")
        return variant

    async def list_movements(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        product_variant_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        catalog_variant_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        movement_type: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[InventoryMovement], int]:
        query = self.movements.search_query(
            product_variant_id=product_variant_id,
            product_id=product_id,
            catalog_variant_id=catalog_variant_id,
            order_id=order_id,
            movement_type=movement_type,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.movements.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def check_stock_available(self, order_id: uuid.UUID) -> list[dict[str, object]]:
        """Pre-flight check before a shipment is created for this order
        (`ShiprocketOperationsService.create_shipment_for_order`) — does
        NOT move any stock (that only ever happens on confirmed dispatch,
        `apply_dispatch`), purely a read. Returns the list of line items
        that resolve to a real variant but don't have enough stock;
        empty means "ok to ship".

        Everything here is in BOXES, and the box requirement per line is
        computed exactly the way `apply_dispatch` will deduct it -- a
        ceiling division of the line's packet quantity by the variant's
        `packets_per_box` -- so this pre-flight check and the eventual
        deduction can never disagree. A zero-quantity line requires
        nothing and is skipped, same as in `apply_dispatch`.

        Mirrors `apply_dispatch`'s own leniency for a SKU that can't be
        resolved to any variant at all (logged there, silently skipped
        here too) — an unresolvable SKU is a data-linking gap, not proof
        of an actual stock shortage, so it must never block a real
        shipment.
        """
        items = await self.order_items.list_for_order(order_id)
        shortages: list[dict[str, object]] = []
        for item in items:
            if item.quantity <= 0:
                continue
            variant = await self._resolve_variant(
                product_variant_id=item.product_variant_id, sku=item.sku
            )
            if variant is None:
                continue
            required_boxes = _ceil_div(
                item.quantity * variant.pack_size, variant.packets_per_box
            )
            if variant.available_quantity < required_boxes:
                shortages.append(
                    {
                        "sku": item.sku,
                        "product_name": item.product_name,
                        "available": variant.available_quantity,
                        "required": required_boxes,
                        "required_packets": item.quantity,
                    }
                )
        return shortages

    # --- automatic movements (Shiprocket-driven only) --------------------

    async def apply_dispatch(self, *, order_id: uuid.UUID, shipment_id: uuid.UUID) -> None:
        """Called once a shipment's courier tracking first reaches a
        dispatched-or-later status (`app.integrations.shiprocket.sync.
        apply_tracking_event`). Decrements every resolvable order line
        item's variant by the box-equivalent of its packet quantity, once
        per (order, variant) -- safe to call again for the same order as
        tracking advances through later statuses (IN_TRANSIT, DELIVERED,
        ...) or on a pull-sync re-scan; never reads anything from Shopify.
        """
        items = await self.order_items.list_for_order(order_id)
        for item in items:
            if item.quantity <= 0:
                continue

            variant = await self._resolve_variant(
                product_variant_id=item.product_variant_id, sku=item.sku
            )
            if variant is None:
                logger.warning(
                    "inventory_dispatch_unresolved_sku", order_id=str(order_id), sku=item.sku
                )
                continue
            # Captured up front: a SAVEPOINT rollback below expires every
            # attribute `begin_nested()` touched, `variant.id` included --
            # under `AsyncSession`, re-reading an expired attribute needs
            # an explicit awaited reload, so a plain `variant.id` access
            # AFTER that rollback (e.g. in the `except` block's log call)
            # raises `MissingGreenlet`. Everything after this point uses
            # `variant_id`, never `variant.id`.
            variant_id = variant.id

            already_moved = await self.movements.exists_for_order(
                order_id=order_id,
                product_variant_id=variant_id,
                movement_type=InventoryMovementType.DISPATCH,
            )
            if already_moved:
                continue

            boxes = _ceil_div(item.quantity * variant.pack_size, variant.packets_per_box)
            new_quantity = variant.available_quantity - boxes
            try:
                # SAVEPOINT, not a bare write: `exists_for_order` above is
                # only a snapshot-time check -- a concurrent transaction
                # (a second tracking event, a webhook racing a pull-sync
                # re-scan) can pass that same check before either commits.
                # The `inventory_movements` unique constraint on
                # (product_variant_id, order_id, movement_type) is the
                # real safety net; a nested transaction here means losing
                # that race only unwinds THIS item's attempted change,
                # never the whole method's already-processed items or the
                # caller's own transaction (see module docstring).
                async with self.session.begin_nested():
                    await self.variants.update(variant, available_quantity=new_quantity)
                    await self.movements.create(
                        product_variant_id=variant_id,
                        movement_type=InventoryMovementType.DISPATCH,
                        quantity_delta=-boxes,
                        quantity_after=new_quantity,
                        order_id=order_id,
                        shipment_id=shipment_id,
                        reason="Shiprocket dispatch",
                    )
            except IntegrityError:
                logger.info(
                    "inventory_dispatch_lost_race",
                    order_id=str(order_id),
                    product_variant_id=str(variant_id),
                )
                continue

        # Unconditional, even when nothing moved -- a read-only SELECT
        # (`order_items.list_for_order`) still opens a transaction, and
        # leaving it dangling open corrupts an unrelated shipment's error
        # handling elsewhere in the same `refresh_tracking` loop (a
        # sibling shipment's `session.rollback()` would then expire this
        # transaction's objects too). Matches the always-commit pattern
        # already used by `ShipmentService.update_shipment`/`RTOService.
        # update_rto`.
        await self.session.commit()

    async def apply_rto_restock(self, *, order_id: uuid.UUID, rto_id: uuid.UUID) -> None:
        """Mirror of `apply_dispatch`, called once a shipment's RTO is
        confirmed received back at the warehouse (`RTOStatus.RECEIVED`) --
        either derived automatically from tracking or set manually via
        `RTOService.update_rto`.

        Restocks the EXACT box quantity this order+variant's own
        `DISPATCH` movement removed, read back from the ledger -- not a
        fresh `packets / packets_per_box` conversion. `packets_per_box`
        is editable at any time (`update_packets_per_box`), and it must
        never retroactively change what an already-recorded dispatch is
        worth: if 120 packets were dispatched at 60/box (-2 boxes) and
        `packets_per_box` is later changed to 30, the RTO restock must
        still be +2 boxes, not +4. Falls back to a fresh conversion using
        the variant's current `packets_per_box` only when no matching
        dispatch was ever recorded through this OMS (a historical/edge
        case) -- the best information available at that point.
        """
        items = await self.order_items.list_for_order(order_id)
        for item in items:
            if item.quantity <= 0:
                continue

            variant = await self._resolve_variant(
                product_variant_id=item.product_variant_id, sku=item.sku
            )
            if variant is None:
                logger.warning(
                    "inventory_rto_restock_unresolved_sku", order_id=str(order_id), sku=item.sku
                )
                continue
            # See the matching comment in `apply_dispatch` -- captured up
            # front so the `except` block never touches a possibly-expired
            # `variant`.
            variant_id = variant.id

            already_moved = await self.movements.exists_for_order(
                order_id=order_id,
                product_variant_id=variant_id,
                movement_type=InventoryMovementType.RTO_RESTOCK,
            )
            if already_moved:
                continue

            dispatch_movement = await self.movements.get_for_order(
                order_id=order_id,
                product_variant_id=variant_id,
                movement_type=InventoryMovementType.DISPATCH,
            )
            if dispatch_movement is not None:
                boxes = abs(dispatch_movement.quantity_delta)
            else:
                boxes = _ceil_div(item.quantity * variant.pack_size, variant.packets_per_box)

            new_quantity = variant.available_quantity + boxes
            try:
                # See the matching comment in `apply_dispatch` -- same
                # SAVEPOINT + unique-constraint race protection.
                async with self.session.begin_nested():
                    await self.variants.update(variant, available_quantity=new_quantity)
                    await self.movements.create(
                        product_variant_id=variant_id,
                        movement_type=InventoryMovementType.RTO_RESTOCK,
                        quantity_delta=boxes,
                        quantity_after=new_quantity,
                        order_id=order_id,
                        rto_id=rto_id,
                        reason="Shiprocket RTO received",
                    )
            except IntegrityError:
                logger.info(
                    "inventory_rto_restock_lost_race",
                    order_id=str(order_id),
                    product_variant_id=str(variant_id),
                )
                continue

        # See the matching comment in `apply_dispatch` -- always commit,
        # even on a no-op pass.
        await self.session.commit()

    # --- staff-initiated writes ------------------------------------------

    async def add_stock(
        self, variant_id: uuid.UUID, *, quantity_to_add: int, reason: str, actor: User | None
    ) -> InventoryMovement:
        """Add-incoming-stock manual adjustment: staff enters ONLY the
        quantity being added (e.g. "+100 boxes"), never the resulting
        total -- the new total is computed and recorded here (current +
        quantity_to_add) so the movement ledger always shows both the
        addition and the resulting balance. Never decreases stock -- a
        downward correction is a different, not-yet-supported action.
        """
        if quantity_to_add <= 0:
            raise ValidationError("Quantity to add must be a positive number.")
        if not reason or not reason.strip():
            raise ValidationError("A reason is required for a manual stock adjustment.")

        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        previous_quantity = variant.available_quantity
        new_quantity = previous_quantity + quantity_to_add

        await self.variants.update(variant, available_quantity=new_quantity)
        movement = await self.movements.create(
            product_variant_id=variant.id,
            movement_type=InventoryMovementType.MANUAL_ADJUSTMENT,
            quantity_delta=quantity_to_add,
            quantity_after=new_quantity,
            reason=reason.strip(),
            actor_user_id=actor.id if actor else None,
        )
        await self.audit.record(
            user=actor,
            action="inventory.manual_adjustment",
            entity_type="product_variant",
            entity_id=str(variant.id),
            previous_value={"available_quantity": previous_quantity},
            new_value={"available_quantity": new_quantity},
            metadata={"reason": reason.strip()},
        )
        await self.session.commit()
        return movement

    async def add_product_stock(
        self, product_id: uuid.UUID, *, quantity_to_add: int, reason: str, actor: User | None
    ) -> InventoryMovement:
        """Product-level Add Stock convenience for a product that has
        exactly ONE underlying variant -- forwards to `add_stock` for that
        variant unchanged (same movement, same audit row).

        A product with more than one variant is rejected outright: there
        is no non-arbitrary way to split a single product-level addition
        across variants, and inventing one is explicitly out of scope.
        The client adds stock to each variant line individually via
        `add_stock` instead.
        """
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found.")

        variants = await self.variants.list_for_product(product_id)
        if len(variants) == 0:
            raise NotFoundError("This product has no variants.")
        if len(variants) > 1:
            raise ValidationError(
                "This product has multiple variants; add stock to each variant "
                "individually rather than a single product-level total."
            )

        return await self.add_stock(
            variants[0].id, quantity_to_add=quantity_to_add, reason=reason, actor=actor
        )

    async def get_catalog_variant_total(self, catalog_variant_id: uuid.UUID) -> int:
        """The number shown as an OMS-visible variant's "Current Total
        Stock" -- SUM of every underlying `ProductVariant.available_quantity`
        plus every reconciliation adjustment ever recorded against this
        `CatalogVariant` (see `adjust_catalog_variant_to_target`). Always
        recomputed live from both sources; nothing is cached.
        """
        members = await self.variants.list_for_catalog_variant(catalog_variant_id)
        sku_total = sum(v.available_quantity for v in members)
        adjustment_total = await self.catalog_variant_adjustments.sum_for_catalog_variant(
            catalog_variant_id
        )
        return sku_total + adjustment_total

    async def add_catalog_variant_stock(
        self,
        catalog_variant_id: uuid.UUID,
        *,
        quantity_to_add: int,
        reason: str,
        actor: User | None,
    ) -> CatalogVariantStockAdjustment:
        """Add-incoming-stock manual adjustment against the OMS-visible
        CatalogVariant TOTAL (e.g. Blue Packet's combined 60/120/180
        stock) -- staff enters ONLY the quantity being added, never a
        per-SKU value, and never the resulting total directly.

        This intentionally does NOT touch any underlying `ProductVariant`
        row: there is no non-arbitrary way to decide which pack size a
        generic total change belongs to (the same reasoning
        `add_product_stock` already applies at the product level), so the
        addition is recorded on a separate, CatalogVariant-scoped ledger
        instead of being distributed across 60/120/180. Dispatch, RTO,
        and Shopify sync are completely unaware of this table and
        continue to move only real per-SKU `available_quantity` exactly
        as before -- this is a reconciliation total layered on top, not a
        new inventory source of truth for what can actually be dispatched
        against a specific SKU.
        """
        if quantity_to_add <= 0:
            raise ValidationError("Quantity to add must be a positive number.")
        if not reason or not reason.strip():
            raise ValidationError("A reason is required for a manual stock adjustment.")

        cv = await self.catalog_variants.get_by_id(catalog_variant_id)
        if cv is None:
            raise NotFoundError("Catalog variant not found.")

        previous_total = await self.get_catalog_variant_total(catalog_variant_id)
        new_total = previous_total + quantity_to_add

        adjustment = await self.catalog_variant_adjustments.create(
            catalog_variant_id=cv.id,
            quantity_delta=quantity_to_add,
            quantity_after=new_total,
            reason=reason.strip(),
            actor_user_id=actor.id if actor else None,
        )
        await self.audit.record(
            user=actor,
            action="inventory.catalog_variant_total_adjustment",
            entity_type="catalog_variant",
            entity_id=str(cv.id),
            previous_value={"available_boxes": previous_total},
            new_value={"available_boxes": new_total},
            metadata={"reason": reason.strip()},
        )
        await self.session.commit()
        return adjustment

    async def update_packets_per_box(
        self, variant_id: uuid.UUID, *, packets_per_box: int, actor: User | None
    ) -> ProductVariant:
        """Changes ONLY the packets<->boxes conversion/display for this
        variant -- never touches `available_quantity`. A box count of 20
        stays 20 boxes before and after this call; only what "20 boxes"
        displays as in packets changes.
        """
        if packets_per_box <= 0:
            raise ValidationError("Packets per box must be a positive integer.")

        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        previous = variant.packets_per_box
        if previous == packets_per_box:
            return variant

        await self.variants.update(variant, packets_per_box=packets_per_box)
        await self.audit.record(
            user=actor,
            action="inventory.packets_per_box_updated",
            entity_type="product_variant",
            entity_id=str(variant.id),
            previous_value={"packets_per_box": previous},
            new_value={"packets_per_box": packets_per_box},
        )
        await self.session.commit()
        return variant

    async def update_pack_size(
        self, variant_id: uuid.UUID, *, pack_size: int, actor: User | None
    ) -> ProductVariant:
        """Changes ONLY how many packets one unit of this variant, as
        ordered, represents -- never touches `available_quantity`. Affects
        future dispatch/RTO box math only (see `apply_dispatch`); no past
        `InventoryMovement` row is ever rewritten.
        """
        if pack_size <= 0:
            raise ValidationError("Pack size must be a positive integer.")

        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        previous = variant.pack_size
        if previous == pack_size:
            return variant

        await self.variants.update(variant, pack_size=pack_size)
        await self.audit.record(
            user=actor,
            action="inventory.pack_size_updated",
            entity_type="product_variant",
            entity_id=str(variant.id),
            previous_value={"pack_size": previous},
            new_value={"pack_size": pack_size},
        )
        await self.session.commit()
        return variant

    async def set_product_display_name(
        self, product_id: uuid.UUID, *, name: str | None, actor: User | None
    ) -> Product:
        """Set (`name` non-empty) or clear (`name` None/blank ->
        "Reset to Shopify Name") `Product.title_override` -- the custom
        display name the Inventory UI shows in place of the Shopify
        `title`. Touches ONLY `title_override`: never `title` (Shopify's
        own value), SKUs, quantities, prices, or any variant row. No
        `InventoryMovement` is written -- this is catalog/presentation
        data, not stock.
        """
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found.")

        normalized = name.strip() if name and name.strip() else None
        previous = product.title_override
        if previous == normalized:
            return product

        await self.products.update(product, title_override=normalized)
        await self.audit.record(
            user=actor,
            action="inventory.product_name_override_updated",
            entity_type="product",
            entity_id=str(product.id),
            previous_value={"title_override": previous},
            new_value={"title_override": normalized},
        )
        await self.session.commit()
        return product

    async def set_variant_display_name(
        self, variant_id: uuid.UUID, *, name: str | None, actor: User | None
    ) -> ProductVariant:
        """Variant counterpart of `set_product_display_name` -- sets or
        clears `ProductVariant.title_override` and nothing else. Never
        touches `title`, `sku`, `available_quantity`, `inventory_quantity`,
        `packets_per_box`, `price`, or the movement ledger.
        """
        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        normalized = name.strip() if name and name.strip() else None
        previous = variant.title_override
        if previous == normalized:
            return variant

        await self.variants.update(variant, title_override=normalized)
        await self.audit.record(
            user=actor,
            action="inventory.variant_name_override_updated",
            entity_type="product_variant",
            entity_id=str(variant.id),
            previous_value={"title_override": previous},
            new_value={"title_override": normalized},
        )
        await self.session.commit()
        return variant

    async def _resolve_variant(
        self, *, product_variant_id: uuid.UUID | None, sku: str
    ) -> ProductVariant | None:
        """`OrderItem.product_variant_id` is only populated once the
        variant it refers to has synced from Shopify (see
        `OrderService.upsert_synced_order`) -- for an order synced before
        its product, or a manually-created order, it stays `None` and the
        snapshot `sku` string is the only way back to a variant.
        """
        if product_variant_id is not None:
            variant = await self.variants.get_by_id(product_variant_id)
            if variant is not None:
                return variant
        return await self.variants.get_by_sku(sku)
