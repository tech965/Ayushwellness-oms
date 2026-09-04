from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import selectinload

from app.models.customer import Customer
from app.models.order import Order, OrderItem
from app.models.returns import Return
from app.repositories.base import BaseRepository


class ReturnRepository(BaseRepository[Return]):
    model = Return

    def search_query(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        payment_type: str | None = None,
        customer_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query()
        if q:
            like = f"%{q}%"
            order_match = exists(
                select(1).where(
                    and_(Order.id == Return.order_id, Order.order_number.ilike(like))
                )
            )
            customer_match = exists(
                select(1).where(
                    and_(
                        Order.id == Return.order_id,
                        Customer.id == Order.customer_id,
                        or_(Customer.full_name.ilike(like), Customer.phone.ilike(like)),
                    )
                )
            )
            item_match = exists(
                select(1).where(
                    and_(
                        OrderItem.order_id == Return.order_id, OrderItem.product_name.ilike(like)
                    )
                )
            )
            stmt = stmt.where(or_(order_match, customer_match, item_match))
        if status:
            stmt = stmt.where(Return.status == status)
        if payment_type:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        and_(Order.id == Return.order_id, Order.payment_type == payment_type)
                    )
                )
            )
        if customer_id:
            stmt = stmt.where(Return.customer_id == customer_id)
        if order_id:
            stmt = stmt.where(Return.order_id == order_id)
        if date_from:
            stmt = stmt.where(Return.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Return.created_at <= date_to)
        stmt = stmt.options(
            selectinload(Return.order).selectinload(Order.customer),
            selectinload(Return.order).selectinload(Order.items),
            selectinload(Return.order_item),
        )
        return stmt

    async def list_for_order(self, order_id: uuid.UUID) -> list[Return]:
        stmt = select(Return).where(Return.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
