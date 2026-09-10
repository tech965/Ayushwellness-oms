from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.models.customer import Customer, CustomerAddress
from app.models.order import Order
from app.repositories.base import BaseRepository
from app.schemas.common import PageParams

# A "repeat" customer/order, for every purpose in this codebase (the
# Admin repeat-customers view, its tests): a customer with MORE THAN ONE
# `Order` row (`Order.customer_id` set) -- never based on Shopify's own
# "returning customer" flag (this OMS doesn't sync one) and never
# inferred from phone/email fuzzy-matching across separate `Customer`
# rows. A customer with exactly one order is explicitly NOT repeat.
REPEAT_CUSTOMER_MIN_ORDERS = 2


class CustomerRepository(BaseRepository[Customer]):
    model = Customer

    async def get_by_id_with_addresses(self, id_: uuid.UUID) -> Customer | None:
        stmt = select(Customer).where(Customer.id == id_).options(selectinload(Customer.addresses))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Customer | None:
        stmt = select(Customer).where(Customer.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def search_query(self, *, q: str | None = None):
        stmt = self._base_query()
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Customer.full_name.ilike(pattern),
                    Customer.email.ilike(pattern),
                    Customer.phone.ilike(pattern),
                )
            )
        return stmt

    async def search_repeat_customers(
        self, *, q: str | None, page_params: PageParams
    ) -> tuple[list[tuple[Customer, int, datetime | None, Decimal]], int]:
        """Customers with `>= REPEAT_CUSTOMER_MIN_ORDERS` real `Order`
        rows, most orders first -- `(customer, order_count,
        latest_order_at, total_order_value)` tuples, paginated. Exactly
        two queries total regardless of page size (this one, plus the
        caller's one follow-up query for per-order detail) -- never one
        query per customer.
        """
        order_counts = (
            select(
                Order.customer_id.label("customer_id"),
                func.count(Order.id).label("order_count"),
                func.max(Order.order_datetime).label("latest_order_at"),
                func.coalesce(func.sum(Order.total_amount), 0).label("total_value"),
            )
            .where(Order.customer_id.is_not(None))
            .group_by(Order.customer_id)
            .having(func.count(Order.id) >= REPEAT_CUSTOMER_MIN_ORDERS)
            .subquery()
        )

        stmt = select(
            Customer,
            order_counts.c.order_count,
            order_counts.c.latest_order_at,
            order_counts.c.total_value,
        ).join(order_counts, order_counts.c.customer_id == Customer.id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(Customer.full_name.ilike(pattern), Customer.phone.ilike(pattern))
            )

        total = (
            await self.session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()

        stmt = (
            stmt.order_by(order_counts.c.order_count.desc(), Customer.full_name)
            .limit(page_params.page_size)
            .offset(page_params.offset)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1], row[2], Decimal(row[3])) for row in rows], total


class CustomerAddressRepository(BaseRepository[CustomerAddress]):
    model = CustomerAddress

    async def list_for_customer(self, customer_id: uuid.UUID) -> list[CustomerAddress]:
        stmt = select(CustomerAddress).where(CustomerAddress.customer_id == customer_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
