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

`OrderItem.quantity` is in PACKETS. Each variant configures its own
`packets_per_box` (never a hardcoded constant); a dispatch/restock
converts packets -> boxes via ceiling division (`_ceil_div`) -- a
partially-consumed box still consumes one whole box-equivalent of
physical stock.

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
from typing import Any

from sqlalchemy import select  # type: ignore[reportMissingImports]
from sqlalchemy.exc import IntegrityError  # type: ignore[reportMissingImports]

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.auth import User
from app.models.enums import InventoryMovementType, StockStatus
from app.models.inventory import InventoryMovement
from app.models.product import Product, ProductVariant
from app.models.settings import AppSettings
from app.repositories.inventory import (
    InventoryMovementRepository,
    InventoryProductRepository,
    InventoryStockRepository,
)
from app.repositories.order import OrderItemRepository
from app.repositories.product import ProductVariantRepository
from app.schemas.common import PageParams, SortParams
from app.schemas.settings import AppSettingsData
from app.services.audit_service import AuditService

logger = get_logger(__name__)


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
        self.movements = InventoryMovementRepository(session)
        self.order_items = OrderItemRepository(session)
        self.audit = AuditService(session)

    # --- reads ----------------------------------------------------------

    async def get_low_stock_threshold(self) -> int:
        row = (
            await self.session.execute(select(AppSettings).limit(1))
        ).scalar_one_or_none()
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
        order_id: uuid.UUID | None = None,
        movement_type: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[InventoryMovement], int]:
        query = self.movements.search_query(
            product_variant_id=product_variant_id,
            product_id=product_id,
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
            required_boxes = _ceil_div(item.quantity, variant.packets_per_box)
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

            boxes = _ceil_div(item.quantity, variant.packets_per_box)
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
                boxes = _ceil_div(item.quantity, variant.packets_per_box)

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

    async def adjust_to_target(
        self, variant_id: uuid.UUID, *, target_boxes: int, reason: str, actor: User | None
    ) -> InventoryMovement:
        """Absolute-target manual adjustment: staff enters the new total
        (e.g. "25 boxes"), never a raw delta -- the delta is computed and
        recorded here so the movement ledger always shows both the
        intent (previous -> new) and the resulting change.
        """
        if target_boxes < 0:
            raise ValidationError("Target stock cannot be negative.")
        if not reason or not reason.strip():
            raise ValidationError("A reason is required for a manual stock adjustment.")

        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        previous_quantity = variant.available_quantity
        delta = target_boxes - previous_quantity
        if delta == 0:
            raise ValidationError("New stock must be different from the current stock.")

        await self.variants.update(variant, available_quantity=target_boxes)
        movement = await self.movements.create(
            product_variant_id=variant.id,
            movement_type=InventoryMovementType.MANUAL_ADJUSTMENT,
            quantity_delta=delta,
            quantity_after=target_boxes,
            reason=reason.strip(),
            actor_user_id=actor.id if actor else None,
        )
        await self.audit.record(
            user=actor,
            action="inventory.manual_adjustment",
            entity_type="product_variant",
            entity_id=str(variant.id),
            previous_value={"available_quantity": previous_quantity},
            new_value={"available_quantity": target_boxes},
            metadata={"reason": reason.strip()},
        )
        await self.session.commit()
        return movement

    async def adjust_product_to_target(
        self, product_id: uuid.UUID, *, target_boxes: int, reason: str, actor: User | None
    ) -> InventoryMovement:
        """Product-level Edit Stock convenience for a product that has
        exactly ONE underlying variant -- forwards to `adjust_to_target`
        for that variant unchanged (same movement, same audit row, same
        negative-stock rejection).

        A product with more than one variant is rejected outright: there
        is no non-arbitrary way to split a single product-level box
        target across variants, and inventing one is explicitly out of
        scope. The client adjusts each variant line individually via
        `adjust_to_target` instead.
        """
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found.")

        variants = await self.variants.list_for_product(product_id)
        if len(variants) == 0:
            raise NotFoundError("This product has no variants.")
        if len(variants) > 1:
            raise ValidationError(
                "This product has multiple variants; adjust each variant's stock "
                "individually rather than setting a single product-level total."
            )

        return await self.adjust_to_target(
            variants[0].id, target_boxes=target_boxes, reason=reason, actor=actor
        )

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
