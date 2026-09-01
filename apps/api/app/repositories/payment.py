from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models.customer import Customer
from app.models.order import Order
from app.models.payment import Payment, PaymentTransaction
from app.repositories.base import BaseRepository

# Eager-loads the relationships the payments dashboard needs on every row
# (order number, customer name/phone/email) so `PaymentService.list_payments`
# never issues a per-row query — the same N+1-avoidance convention
# `OrderRepository.get_by_id_with_items_and_customer` already established.
_WITH_ORDER_AND_CUSTOMER = selectinload(Payment.order).selectinload(Order.customer)


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def list_for_order(self, order_id: uuid.UUID) -> list[Payment]:
        stmt = select(Payment).where(Payment.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def search_query(
        self,
        *,
        order_id: uuid.UUID | None = None,
        provider: str | None = None,
        status: str | None = None,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        payment_method: str | None = None,
    ):
        """`order_id` is the original (Phase 1) filter — every existing
        caller that only ever passed `order_id` keeps working unchanged,
        it's just narrowed further when the new, optional filters below
        are also supplied. All new filters default to `None` (no-op).

        `payment_method` matches `Payment.payment_metadata["payment_method"]`
        — the field `CashfreePaymentService.apply_payment_event` already
        writes there (see `app/services/cashfree_payment_service.py`).
        SQLAlchemy's JSON comparator (`[...].as_string()`) compiles to the
        right SQL per dialect (`json_extract` on SQLite, `->>` on
        Postgres/JSONB) — no hand-rolled dialect branching needed.
        """
        stmt = self._base_query().options(_WITH_ORDER_AND_CUSTOMER)
        if order_id is not None:
            stmt = stmt.where(Payment.order_id == order_id)
        if provider:
            stmt = stmt.where(Payment.provider == provider)
        if status:
            stmt = stmt.where(Payment.status == status)
        if date_from:
            stmt = stmt.where(Payment.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Payment.created_at <= date_to)
        if payment_method:
            stmt = stmt.where(
                Payment.payment_metadata["payment_method"].as_string() == payment_method
            )
        if q:
            like = f"%{q}%"
            # Matches this payment's own gateway order id/transaction id
            # directly, or (via EXISTS, so a payment never produces
            # duplicate rows) its linked order's order_number or that
            # order's customer's name/phone/email — the same "search by
            # order number / customer name / phone / email" convention
            # `OrderRepository.search_query` already uses.
            order_match = exists(
                select(1).where(
                    and_(Order.id == Payment.order_id, Order.order_number.ilike(like))
                )
            )
            customer_match = exists(
                select(1).where(
                    and_(
                        Order.id == Payment.order_id,
                        Customer.id == Order.customer_id,
                        or_(
                            Customer.full_name.ilike(like),
                            Customer.phone.ilike(like),
                            Customer.email.ilike(like),
                        ),
                    )
                )
            )
            stmt = stmt.where(
                or_(
                    Payment.external_id.ilike(like),
                    Payment.external_transaction_id.ilike(like),
                    order_match,
                    customer_match,
                )
            )
        return stmt

    async def get_by_id_with_transactions(self, id_: uuid.UUID) -> Payment | None:
        stmt = (
            select(Payment)
            .where(Payment.id == id_)
            .options(selectinload(Payment.transactions), _WITH_ORDER_AND_CUSTOMER)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_export(self, query, *, limit: int) -> list[Payment]:
        """Runs `query` (from `search_query()`, already eager-loading
        order/customer) unpaginated, capped at `limit` — mirrors
        `OrderRepository.list_for_export`.
        """
        stmt = query.order_by(Payment.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


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
