from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enums import InventoryMovementType
from app.models.inventory import InventoryMovement
from app.models.product import Product, ProductVariant
from app.repositories.base import AppendOnlyRepository, BaseRepository


class InventoryMovementRepository(AppendOnlyRepository[InventoryMovement]):
    model = InventoryMovement

    async def exists_for_order(
        self,
        *,
        order_id: uuid.UUID,
        product_variant_id: uuid.UUID,
        movement_type: InventoryMovementType,
    ) -> bool:
        """The idempotency check `InventoryService.apply_dispatch`/
        `apply_rto_restock` run before writing -- both hook points can
        fire more than once for the same order (repeated tracking events,
        a pull-sync re-scanning a shipment already at the same status), so
        a movement for this exact (order, variant, type) must never be
        written twice.
        """
        stmt = select(InventoryMovement.id).where(
            InventoryMovement.order_id == order_id,
            InventoryMovement.product_variant_id == product_variant_id,
            InventoryMovement.movement_type == movement_type,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    def search_query(
        self,
        *,
        product_variant_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        movement_type: str | None = None,
    ):
        stmt = self._base_query().options(selectinload(InventoryMovement.product_variant))
        if product_variant_id:
            stmt = stmt.where(InventoryMovement.product_variant_id == product_variant_id)
        if order_id:
            stmt = stmt.where(InventoryMovement.order_id == order_id)
        if movement_type:
            stmt = stmt.where(InventoryMovement.movement_type == movement_type)
        return stmt


class InventoryStockRepository(BaseRepository[ProductVariant]):
    """Read-side view over `ProductVariant` for the stock-levels listing --
    intentionally not `ProductVariantRepository` (`app.repositories.product`),
    which owns variant CRUD/sync; this repository only ever reads.
    """

    model = ProductVariant

    async def get_by_id_with_product(self, id_: uuid.UUID) -> ProductVariant | None:
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.id == id_)
            .options(selectinload(ProductVariant.product))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def search_query(self, *, q: str | None = None, low_stock_only: bool = False):
        stmt = self._base_query().join(Product).options(selectinload(ProductVariant.product))
        if q:
            stmt = stmt.where(
                (ProductVariant.sku.ilike(f"%{q}%")) | (Product.title.ilike(f"%{q}%"))
            )
        if low_stock_only:
            stmt = stmt.where(ProductVariant.available_quantity <= 0)
        return stmt
