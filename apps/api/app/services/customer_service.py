"""Customer CRUD plus the Customer 360 summary.

Summary values are derived from transactional Order/RTO data on read,
never duplicated as mutable totals on Customer — see spec §12.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.customer import Customer
from app.models.enums import OrderStatus
from app.models.order import Order
from app.models.rto import RTO
from app.repositories.customer import CustomerAddressRepository, CustomerRepository
from app.repositories.order import OrderRepository
from app.schemas.common import PageParams, SortParams
from app.schemas.customer import CustomerSummaryResponse


class CustomerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.orders = OrderRepository(session)
        self.addresses = CustomerAddressRepository(session)

    async def list_customers(
        self, *, page_params: PageParams, sort_params: SortParams, q: str | None = None
    ) -> tuple[list[Customer], int]:
        query = self.customers.search_query(q=q)
        items, total = await self.customers.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_customer(self, customer_id: uuid.UUID) -> Customer:
        customer = await self.customers.get_by_id(customer_id)
        if customer is None:
            raise NotFoundError("Customer not found.")
        return customer

    async def create_customer(self, **fields) -> Customer:  # noqa: ANN003
        fields["full_name"] = (
            fields.get("full_name")
            or " ".join(filter(None, [fields.get("first_name"), fields.get("last_name")]))
            or None
        )
        customer = await self.customers.create(**fields, source_system="manual")
        await self.session.commit()
        return customer

    async def update_customer(self, customer_id: uuid.UUID, **fields) -> Customer:  # noqa: ANN003
        customer = await self.get_customer(customer_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            await self.customers.update(customer, **clean)
        await self.session.commit()
        return customer

    async def get_orders_for_customer(
        self, customer_id: uuid.UUID, *, page_params: PageParams, sort_params: SortParams
    ) -> tuple[list[Order], int]:
        await self.get_customer(customer_id)
        query = self.orders.for_customer_query(customer_id)
        items, total = await self.orders.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_summary(self, customer_id: uuid.UUID) -> CustomerSummaryResponse:
        await self.get_customer(customer_id)

        stmt = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_amount), 0),
            func.coalesce(func.sum(case((Order.status == OrderStatus.DELIVERED, 1), else_=0)), 0),
            func.coalesce(func.sum(case((Order.status == OrderStatus.CANCELLED, 1), else_=0)), 0),
            func.max(Order.order_datetime),
        ).where(Order.customer_id == customer_id)
        total_orders, total_spent, delivered, cancelled, last_order_at = (
            await self.session.execute(stmt)
        ).one()

        rto_stmt = (
            select(func.count(func.distinct(RTO.order_id)))
            .select_from(RTO)
            .join(Order, RTO.order_id == Order.id)
            .where(Order.customer_id == customer_id)
        )
        rto_orders = (await self.session.execute(rto_stmt)).scalar() or 0

        total_spent = Decimal(total_spent or 0)
        average_order_value = (total_spent / total_orders) if total_orders else Decimal("0")

        return CustomerSummaryResponse(
            customer_id=customer_id,
            total_orders=total_orders or 0,
            total_spent=total_spent,
            delivered_orders=delivered or 0,
            cancelled_orders=cancelled or 0,
            rto_orders=rto_orders,
            average_order_value=average_order_value,
            last_order_at=last_order_at,
        )

    async def upsert_synced_customer(self, **data) -> tuple[Customer, bool]:  # noqa: ANN003
        """Idempotent create-or-update from a sync adapter's normalized
        customer dict (`source_system`/`external_id` required). Shopify is
        the source of truth for every field here — this never touches
        OMS-only fields (`notes`, `is_active` is the one exception, since
        Shopify's customer `state` genuinely reflects account status).
        """
        addresses = data.pop("addresses", [])
        source_system = data.pop("source_system")
        external_id = data.pop("external_id")

        data["full_name"] = (
            data.get("full_name")
            or " ".join(filter(None, [data.get("first_name"), data.get("last_name")]))
            or None
        )

        customer, created = await self.customers.upsert_by_external_id(
            source_system=source_system, external_id=external_id, **data
        )

        for address in addresses:
            address_external_id = address.pop("external_id", None)
            if not address_external_id:
                continue
            await self.addresses.upsert_by_external_id(
                source_system=source_system,
                external_id=address_external_id,
                customer_id=customer.id,
                **address,
            )

        await self.session.commit()
        return customer, created
