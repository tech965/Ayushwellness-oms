from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

    async def get_by_gateway_transaction_id(
        self, *, gateway: str, gateway_transaction_id: str
    ) -> PaymentTransaction | None:
        stmt = select(PaymentTransaction).where(
            PaymentTransaction.gateway == gateway,
            PaymentTransaction.gateway_transaction_id == gateway_transaction_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_if_new(
        self, *, gateway: str, gateway_transaction_id: str | None, **fields: object
    ) -> tuple[PaymentTransaction, bool]:
        """Idempotent create keyed by `(gateway, gateway_transaction_id)`
        — the defense-in-depth backstop described on
        `PaymentTransaction.__table_args__`. `gateway_transaction_id=None`
        always creates a fresh row (there's nothing to dedupe against —
        matches `Shipment.awb`'s existing nullable-unique convention).
        Mirrors `BaseRepository.upsert_by_external_id`'s
        check-then-create-with-IntegrityError-retry shape.
        """
        if gateway_transaction_id is not None:
            existing = await self.get_by_gateway_transaction_id(
                gateway=gateway, gateway_transaction_id=gateway_transaction_id
            )
            if existing is not None:
                return existing, False

        try:
            instance = await self.create(
                gateway=gateway, gateway_transaction_id=gateway_transaction_id, **fields
            )
            return instance, True
        except IntegrityError:
            await self.session.rollback()
            if gateway_transaction_id is None:
                raise
            existing = await self.get_by_gateway_transaction_id(
                gateway=gateway, gateway_transaction_id=gateway_transaction_id
            )
            if existing is None:
                raise
            return existing, False
