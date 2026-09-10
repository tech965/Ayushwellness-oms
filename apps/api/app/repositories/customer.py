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

    @staticmethod
    def _repeat_order_counts_subquery():  # noqa: ANN205
        """Per-customer real order counts/aggregates for every customer
        with `>= REPEAT_CUSTOMER_MIN_ORDERS` orders -- the ONE query
        "repeat customer" is defined by anywhere in this codebase.
        Deliberately never date-scoped: a repeat customer is one with
        that many orders across their whole history, not within any
        particular window (see `CustomerRepository.
        count_repeat_customers_in_range`'s docstring for how a dashboard
        date range still applies -- to when the *customer* was created,
        never to which of their orders count towards this total).
        Reused by both `search_repeat_customers` (Customers -> Repeat
        Customers page) and `count_repeat_customers_in_range` (the
        Admin Dashboard KPI) so there is only ever one definition.
        """
        return (
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

    async def count_repeat_customers_in_range(
        self, *, date_from: datetime, date_to: datetime
    ) -> int:
        """The Admin Dashboard "Repeat Customers" KPI: customers whose
        `Customer.created_at` falls in `[date_from, date_to]` who ALSO
        have `>= REPEAT_CUSTOMER_MIN_ORDERS` real orders.

        Date semantics deliberately mirror the dashboard's existing
        "Total Customers" KPI exactly (`AnalyticsService._summary_counts`:
        `Customer.created_at` in range, "new customers" in the period,
        not an all-time running total) -- so `repeat_customers /
        total_customers` is always a same-cohort percentage, never two
        differently-scoped numbers divided against each other. The
        `>= REPEAT_CUSTOMER_MIN_ORDERS` check itself stays all-time/
        unscoped (`_repeat_order_counts_subquery`) -- a customer's repeat
        status is never redefined per dashboard window, only WHICH
        customers are being asked about is.

        One aggregate query -- no customers/orders fetched into Python.
        """
        order_counts = self._repeat_order_counts_subquery()
        stmt = (
            select(func.count())
            .select_from(Customer)
            .join(order_counts, order_counts.c.customer_id == Customer.id)
            .where(Customer.created_at >= date_from, Customer.created_at <= date_to)
        )
        return int(await self.session.scalar(stmt) or 0)

    async def search_repeat_customers(
        self, *, q: str | None, page_params: PageParams
    ) -> tuple[list[tuple[Customer, int, datetime | None, Decimal]], int]:
        """Customers with `>= REPEAT_CUSTOMER_MIN_ORDERS` real `Order`
        rows, most orders first -- `(customer, order_count,
        latest_order_at, total_order_value)` tuples, paginated. Exactly
        two queries total regardless of page size (this one, plus the
        caller's one follow-up query for per-order detail) -- never one
        query per customer. All-time/unfiltered by design -- see
        `_repeat_order_counts_subquery`'s docstring; unaffected by
        `count_repeat_customers_in_range`'s dashboard date scoping.
        """
        order_counts = self._repeat_order_counts_subquery()

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
