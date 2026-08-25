from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderEvent, OrderItem
from app.repositories.base import AppendOnlyRepository, BaseRepository


class OrderRepository(BaseRepository[Order]):
    model = Order

    async def get_by_order_number(self, order_number: str) -> Order | None:
        stmt = select(Order).where(Order.order_number == order_number)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_items(self, id_: uuid.UUID) -> Order | None:
        stmt = select(Order).where(Order.id == id_).options(selectinload(Order.items))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_items_and_customer(self, id_: uuid.UUID) -> Order | None:
        """Eager-loads `items` and `customer` — needed by any caller that
        touches `order.customer` (e.g.
        `ShiprocketOperationsService.create_shipment_for_order`), since a
        lazy load on an un-loaded relationship raises `MissingGreenlet`
        under `AsyncSession`.
        """
        stmt = (
            select(Order)
            .where(Order.id == id_)
            .options(selectinload(Order.items), selectinload(Order.customer))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def search_query(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        payment_status: str | None = None,
        customer_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query()
        if q:
            stmt = stmt.where(
                or_(Order.order_number.ilike(f"%{q}%"), Order.shopify_order_id.ilike(f"%{q}%"))
            )
        if status:
            stmt = stmt.where(Order.status == status)
        if payment_status:
            stmt = stmt.where(Order.payment_status == payment_status)
        if customer_id:
            stmt = stmt.where(Order.customer_id == customer_id)
        if date_from:
            stmt = stmt.where(Order.order_datetime >= date_from)
        if date_to:
            stmt = stmt.where(Order.order_datetime <= date_to)
        return stmt

    def for_customer_query(self, customer_id: uuid.UUID):
        return self._base_query().where(Order.customer_id == customer_id)


class OrderItemRepository(BaseRepository[OrderItem]):
    model = OrderItem

    async def list_for_order(self, order_id: uuid.UUID) -> list[OrderItem]:
        stmt = select(OrderItem).where(OrderItem.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrderEventRepository(AppendOnlyRepository[OrderEvent]):
    model = OrderEvent

    async def list_for_order(self, order_id: uuid.UUID) -> list[OrderEvent]:
        stmt = (
            select(OrderEvent)
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
