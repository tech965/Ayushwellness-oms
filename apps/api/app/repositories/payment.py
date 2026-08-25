from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.payment import Payment, PaymentTransaction
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def list_for_order(self, order_id: uuid.UUID) -> list[Payment]:
        stmt = select(Payment).where(Payment.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def search_query(self, *, order_id: uuid.UUID | None = None):
        stmt = self._base_query()
        if order_id is not None:
            stmt = stmt.where(Payment.order_id == order_id)
        return stmt

    async def get_by_id_with_transactions(self, id_: uuid.UUID) -> Payment | None:
        stmt = select(Payment).where(Payment.id == id_).options(selectinload(Payment.transactions))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class PaymentTransactionRepository(BaseRepository[PaymentTransaction]):
    model = PaymentTransaction

    async def list_for_payment(self, payment_id: uuid.UUID) -> list[PaymentTransaction]:
        stmt = select(PaymentTransaction).where(PaymentTransaction.payment_id == payment_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
