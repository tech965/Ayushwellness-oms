from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import selectinload

from app.models.customer import Customer
from app.models.ndr import NDR
from app.models.order import Order, OrderItem
from app.repositories.base import BaseRepository


class NDRRepository(BaseRepository[NDR]):
    model = NDR

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
            # Matches order number, linked customer's name/phone, or any
            # of the order's line-item product names -- same EXISTS-based
            # pattern as `OrderRepository.search_query` (never loads every
            # row into Python to filter).
            order_match = exists(
                select(1).where(and_(Order.id == NDR.order_id, Order.order_number.ilike(like)))
            )
            customer_match = exists(
                select(1).where(
                    and_(
                        Order.id == NDR.order_id,
                        Customer.id == Order.customer_id,
                        or_(Customer.full_name.ilike(like), Customer.phone.ilike(like)),
                    )
                )
            )
            item_match = exists(
                select(1).where(
                    and_(OrderItem.order_id == NDR.order_id, OrderItem.product_name.ilike(like))
                )
            )
            stmt = stmt.where(or_(order_match, customer_match, item_match))
        if status:
            stmt = stmt.where(NDR.status == status)
        if payment_type:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        and_(Order.id == NDR.order_id, Order.payment_type == payment_type)
                    )
                )
            )
        if courier_id:
            stmt = stmt.where(NDR.courier_id == courier_id)
        if date_from:
            stmt = stmt.where(NDR.created_at >= date_from)
        if date_to:
            stmt = stmt.where(NDR.created_at <= date_to)
        # Every caller of `search_query` serializes through `NDRListResponse`,
        # which needs order/customer/product/shipment data — eager-loaded
        # once here (matching `OrderRepository.search_query`'s own
        # convention) so no caller has to remember to, and no lazy-load on
        # an `AsyncSession`-backed relationship can raise `MissingGreenlet`.
        stmt = stmt.options(
            selectinload(NDR.order).selectinload(Order.customer),
            selectinload(NDR.order).selectinload(Order.items),
            selectinload(NDR.shipment),
            selectinload(NDR.courier),
        )
        return stmt

    async def list_for_shipment(self, shipment_id: uuid.UUID) -> list[NDR]:
        stmt = select(NDR).where(NDR.shipment_id == shipment_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
