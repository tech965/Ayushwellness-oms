from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.models.customer import Customer, CustomerAddress
from app.repositories.base import BaseRepository


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


class CustomerAddressRepository(BaseRepository[CustomerAddress]):
    model = CustomerAddress

    async def list_for_customer(self, customer_id: uuid.UUID) -> list[CustomerAddress]:
        stmt = select(CustomerAddress).where(CustomerAddress.customer_id == customer_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
