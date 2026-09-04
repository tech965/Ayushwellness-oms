"""OMS-authoritative stock tracking.

`ProductVariant.available_quantity` starts out seeded from Shopify (see
`ProductService.upsert_synced_product`) and from then on is only ever
moved from here: down on dispatch, up on RTO restock, or by a staff
manual adjustment. Every move writes exactly one `InventoryMovement` row
-- the ledger is the audit trail and, for the two automatic movement
types, also the idempotency guard (see `apply_dispatch`/
`apply_rto_restock`).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.auth import User
from app.models.enums import InventoryMovementType
from app.models.inventory import InventoryMovement
from app.models.product import ProductVariant
from app.repositories.inventory import InventoryMovementRepository, InventoryStockRepository
from app.repositories.order import OrderItemRepository
from app.repositories.product import ProductVariantRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService

logger = get_logger(__name__)


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.stock = InventoryStockRepository(session)
        self.variants = ProductVariantRepository(session)
        self.movements = InventoryMovementRepository(session)
        self.order_items = OrderItemRepository(session)
        self.audit = AuditService(session)

    async def list_stock(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        low_stock_only: bool = False,
    ) -> tuple[list[ProductVariant], int]:
        query = self.stock.search_query(q=q, low_stock_only=low_stock_only)
        items, total = await self.stock.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

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
        order_id: uuid.UUID | None = None,
        movement_type: str | None = None,
    ) -> tuple[list[InventoryMovement], int]:
        query = self.movements.search_query(
            product_variant_id=product_variant_id, order_id=order_id, movement_type=movement_type
        )
        items, total = await self.movements.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def apply_dispatch(self, *, order_id: uuid.UUID, shipment_id: uuid.UUID) -> None:
        """Called once a shipment's courier tracking first reaches a
        dispatched-or-later status (`app.integrations.shiprocket.sync.
        apply_tracking_event`). Decrements every resolvable order line
        item's variant, once per (order, variant) -- safe to call again
        for the same order as tracking advances through later statuses
        (IN_TRANSIT, DELIVERED, ...) or on a pull-sync re-scan.
        """
        items = await self.order_items.list_for_order(order_id)
        for item in items:
            variant = await self._resolve_variant(
                product_variant_id=item.product_variant_id, sku=item.sku
            )
            if variant is None:
                logger.warning(
                    "inventory_dispatch_unresolved_sku", order_id=str(order_id), sku=item.sku
                )
                continue

            already_moved = await self.movements.exists_for_order(
                order_id=order_id,
                product_variant_id=variant.id,
                movement_type=InventoryMovementType.DISPATCH,
            )
            if already_moved:
                continue

            new_quantity = variant.available_quantity - item.quantity
            await self.variants.update(variant, available_quantity=new_quantity)
            await self.movements.create(
                product_variant_id=variant.id,
                movement_type=InventoryMovementType.DISPATCH,
                quantity_delta=-item.quantity,
                quantity_after=new_quantity,
                order_id=order_id,
                shipment_id=shipment_id,
            )

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
        """
        items = await self.order_items.list_for_order(order_id)
        for item in items:
            variant = await self._resolve_variant(
                product_variant_id=item.product_variant_id, sku=item.sku
            )
            if variant is None:
                logger.warning(
                    "inventory_rto_restock_unresolved_sku", order_id=str(order_id), sku=item.sku
                )
                continue

            already_moved = await self.movements.exists_for_order(
                order_id=order_id,
                product_variant_id=variant.id,
                movement_type=InventoryMovementType.RTO_RESTOCK,
            )
            if already_moved:
                continue

            new_quantity = variant.available_quantity + item.quantity
            await self.variants.update(variant, available_quantity=new_quantity)
            await self.movements.create(
                product_variant_id=variant.id,
                movement_type=InventoryMovementType.RTO_RESTOCK,
                quantity_delta=item.quantity,
                quantity_after=new_quantity,
                order_id=order_id,
                rto_id=rto_id,
            )

        # See the matching comment in `apply_dispatch` -- always commit,
        # even on a no-op pass.
        await self.session.commit()

    async def adjust_manual(
        self, variant_id: uuid.UUID, *, delta: int, reason: str, actor: User | None
    ) -> InventoryMovement:
        if delta == 0:
            raise ValidationError("Adjustment delta must be non-zero.")

        variant = await self.variants.get_by_id(variant_id)
        if variant is None:
            raise NotFoundError("Product variant not found.")

        previous_quantity = variant.available_quantity
        new_quantity = previous_quantity + delta
        await self.variants.update(variant, available_quantity=new_quantity)
        movement = await self.movements.create(
            product_variant_id=variant.id,
            movement_type=InventoryMovementType.MANUAL_ADJUSTMENT,
            quantity_delta=delta,
            quantity_after=new_quantity,
            reason=reason,
            actor_user_id=actor.id if actor else None,
        )
        await self.audit.record(
            user=actor,
            action="inventory.manual_adjustment",
            entity_type="product_variant",
            entity_id=str(variant.id),
            previous_value={"available_quantity": previous_quantity},
            new_value={"available_quantity": new_quantity},
            metadata={"reason": reason},
        )
        await self.session.commit()
        return movement

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
