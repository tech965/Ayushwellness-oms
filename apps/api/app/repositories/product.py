from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.models.product import Product, ProductVariant
from app.repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    model = Product

    async def get_by_id_with_variants(self, id_: uuid.UUID) -> Product | None:
        stmt = select(Product).where(Product.id == id_).options(selectinload(Product.variants))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def search_query(self, *, q: str | None = None, status: str | None = None):
        stmt = self._base_query()
        if q:
            stmt = stmt.where(or_(Product.title.ilike(f"%{q}%"), Product.vendor.ilike(f"%{q}%")))
        if status:
            stmt = stmt.where(Product.status == status)
        return stmt


class ProductVariantRepository(BaseRepository[ProductVariant]):
    model = ProductVariant

    async def get_by_sku(self, sku: str) -> ProductVariant | None:
        stmt = select(ProductVariant).where(ProductVariant.sku == sku)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_product(self, product_id: uuid.UUID) -> list[ProductVariant]:
        stmt = select(ProductVariant).where(ProductVariant.product_id == product_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
