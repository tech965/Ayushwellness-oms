from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.models.enums import InventoryMovementType
from app.models.inventory import InventoryMovement
from app.models.product import Product, ProductVariant
from app.repositories.base import AppendOnlyRepository, BaseRepository


class InventoryMovementRepository(AppendOnlyRepository[InventoryMovement]):
    model = InventoryMovement

    async def get_by_id_with_relations(self, id_: uuid.UUID) -> InventoryMovement | None:
        stmt = (
            select(InventoryMovement)
            .where(InventoryMovement.id == id_)
            .options(
                selectinload(InventoryMovement.product_variant).selectinload(
                    ProductVariant.product
                ),
                selectinload(InventoryMovement.actor),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_order(
        self,
        *,
        order_id: uuid.UUID,
        product_variant_id: uuid.UUID,
        movement_type: InventoryMovementType,
    ) -> InventoryMovement | None:
        """The single row (if any) already recorded for this exact
        (order, variant, movement type) -- used both as the idempotency
        check (`exists_for_order`, below) and, by `InventoryService.
        apply_rto_restock`, to read back a prior `DISPATCH` movement's
        exact box quantity rather than recomputing it (`packets_per_box`
        can change between dispatch and RTO; the restock must not).

        This snapshot-time SELECT is NOT by itself sufficient to prevent
        two concurrent transactions from both proceeding -- see the
        `InventoryService` module docstring for why the DB-level
        `UniqueConstraint` on `inventory_movements` (product_variant_id,
        order_id, movement_type) is the actual safety net, and why a
        `session.begin_nested()` SAVEPOINT around the write is required
        to handle losing that race safely.
        """
        stmt = select(InventoryMovement).where(
            InventoryMovement.order_id == order_id,
            InventoryMovement.product_variant_id == product_variant_id,
            InventoryMovement.movement_type == movement_type,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_for_order(
        self,
        *,
        order_id: uuid.UUID,
        product_variant_id: uuid.UUID,
        movement_type: InventoryMovementType,
    ) -> bool:
        movement = await self.get_for_order(
            order_id=order_id, product_variant_id=product_variant_id, movement_type=movement_type
        )
        return movement is not None

    def search_query(
        self,
        *,
        product_variant_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        movement_type: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query().options(
            selectinload(InventoryMovement.product_variant).selectinload(ProductVariant.product),
            selectinload(InventoryMovement.actor),
        )
        if product_variant_id:
            stmt = stmt.where(InventoryMovement.product_variant_id == product_variant_id)
        if product_id:
            stmt = stmt.join(
                ProductVariant, ProductVariant.id == InventoryMovement.product_variant_id
            ).where(ProductVariant.product_id == product_id)
        if order_id:
            stmt = stmt.where(InventoryMovement.order_id == order_id)
        if movement_type:
            stmt = stmt.where(InventoryMovement.movement_type == movement_type)
        if date_from:
            stmt = stmt.where(InventoryMovement.created_at >= date_from)
        if date_to:
            stmt = stmt.where(InventoryMovement.created_at <= date_to)
        return stmt


class InventoryStockRepository(BaseRepository[ProductVariant]):
    """Read-side view over a single `ProductVariant` -- intentionally not
    `ProductVariantRepository` (`app.repositories.product`), which owns
    variant CRUD/sync; this repository only ever reads.
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


class InventoryProductRepository(BaseRepository[Product]):
    """Product-level read side backing the main Inventory page (Product ->
    Variant -> Inventory hierarchy) -- searches by product title/vendor OR
    any of its variants' SKU, and eager-loads variants so a stock summary
    (variant count, total boxes) can be computed per product with no N+1.
    """

    model = Product

    def search_query(self, *, q: str | None = None):
        stmt = self._base_query().options(selectinload(Product.variants))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Product.title.ilike(like),
                    Product.vendor.ilike(like),
                    Product.variants.any(ProductVariant.sku.ilike(like)),
                )
            )
        return stmt
