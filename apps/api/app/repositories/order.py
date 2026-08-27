from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import selectinload

from app.models.customer import Customer
from app.models.order import Order, OrderEvent, OrderItem
from app.models.shipment import Shipment
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
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        shipment_status: str | None = None,
        courier_id: uuid.UUID | None = None,
        sku: str | None = None,
        amount_min: Decimal | None = None,
        amount_max: Decimal | None = None,
        customer_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        stmt = self._base_query()
        if q:
            like = f"%{q}%"
            # `q` matches order number/Shopify id directly, or (via EXISTS,
            # so an order with multiple shipments never produces duplicate
            # rows) its linked customer's name/phone/email or any of its
            # shipments' AWB — matches spec's "search by order number,
            # customer name, phone, email, tracking number".
            customer_match = exists(
                select(1).where(
                    and_(
                        Customer.id == Order.customer_id,
                        or_(
                            Customer.full_name.ilike(like),
                            Customer.phone.ilike(like),
                            Customer.email.ilike(like),
                        ),
                    )
                )
            )
            shipment_awb_match = exists(
                select(1).where(and_(Shipment.order_id == Order.id, Shipment.awb.ilike(like)))
            )
            stmt = stmt.where(
                or_(
                    Order.order_number.ilike(like),
                    Order.shopify_order_id.ilike(like),
                    customer_match,
                    shipment_awb_match,
                )
            )
        if status:
            stmt = stmt.where(Order.status == status)
        if payment_status:
            stmt = stmt.where(Order.payment_status == payment_status)
        if payment_type:
            stmt = stmt.where(Order.payment_type == payment_type)
        if fulfillment_status:
            stmt = stmt.where(Order.fulfillment_status == fulfillment_status)
        if customer_id:
            stmt = stmt.where(Order.customer_id == customer_id)
        if date_from:
            stmt = stmt.where(Order.order_datetime >= date_from)
        if date_to:
            stmt = stmt.where(Order.order_datetime <= date_to)
        if amount_min is not None:
            stmt = stmt.where(Order.total_amount >= amount_min)
        if amount_max is not None:
            stmt = stmt.where(Order.total_amount <= amount_max)
        if shipment_status or courier_id:
            conditions = [Shipment.order_id == Order.id]
            if shipment_status:
                conditions.append(Shipment.current_status == shipment_status)
            if courier_id:
                conditions.append(Shipment.courier_id == courier_id)
            stmt = stmt.where(exists(select(1).where(and_(*conditions))))
        if sku:
            stmt = stmt.where(
                exists(
                    select(1).where(
                        and_(OrderItem.order_id == Order.id, OrderItem.sku.ilike(f"%{sku}%"))
                    )
                )
            )
        # Every caller of `search_query` eventually serializes through a
        # response that touches `customer`/`items`/`shipments` (list rows
        # need customer/product/shipment columns; export needs all three;
        # even a plain `OrderResponse` caller just harmlessly loads and
        # discards them) — eager-loading once here means no caller has to
        # remember to, and avoids a `MissingGreenlet` from an un-loaded
        # relationship being touched later under `AsyncSession`.
        stmt = stmt.options(
            selectinload(Order.customer),
            selectinload(Order.items),
            selectinload(Order.shipments).selectinload(Shipment.courier),
        )
        return stmt

    def for_customer_query(self, customer_id: uuid.UUID):
        return self._base_query().where(Order.customer_id == customer_id)

    async def list_for_export(self, query, *, limit: int) -> list[Order]:
        """Runs `query` (from `search_query()`, already eager-loading
        `customer`/`items`/`shipments`) unpaginated, capped at `limit` rows
        — used by the orders export endpoint, which has no `page`/
        `page_size` since it streams every filtered row at once.
        `PageParams.page_size` caps at 200 (fine for the UI), too low for a
        multi-thousand-row export, hence this separate path.
        """
        stmt = query.order_by(Order.order_datetime.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


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
