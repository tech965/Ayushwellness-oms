from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import selectinload

from app.models.customer import Customer
from app.models.order import Order, OrderItem
from app.models.rto import RTO
from app.repositories.base import BaseRepository


class RTORepository(BaseRepository[RTO]):
    model = RTO

    def search_query(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        payment_type: str | None = None,
        courier_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query()
        if q:
            like = f"%{q}%"
            order_match = exists(
                select(1).where(and_(Order.id == RTO.order_id, Order.order_number.ilike(like)))
            )
            customer_match = exists(
                select(1).where(
                    and_(
                        Order.id == RTO.order_id,
                        Customer.id == Order.customer_id,
                        or_(Customer.full_name.ilike(like), Customer.phone.ilike(like)),
                    )
                )
            )
            item_match = exists(
                select(1).where(
                    and_(OrderItem.order_id == RTO.order_id, OrderItem.product_name.ilike(like))
                )
            )
            stmt = stmt.where(or_(order_match, customer_match, item_match))
        if status:
            stmt = stmt.where(RTO.status == status)
        if payment_type:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        and_(Order.id == RTO.order_id, Order.payment_type == payment_type)
                    )
                )
            )
        if courier_id:
            stmt = stmt.where(RTO.courier_id == courier_id)
        if date_from:
            stmt = stmt.where(RTO.created_at >= date_from)
        if date_to:
            stmt = stmt.where(RTO.created_at <= date_to)
        stmt = stmt.options(
            selectinload(RTO.order).selectinload(Order.customer),
            selectinload(RTO.order).selectinload(Order.items),
            selectinload(RTO.shipment),
        )
        return stmt

    async def list_for_shipment(self, shipment_id: uuid.UUID) -> list[RTO]:
        stmt = select(RTO).where(RTO.shipment_id == shipment_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
