"""Read-only in Phase 1 — no live payment gateway. Payment rows are
created by `OrderService.create_order`.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.payment import Payment
from app.repositories.payment import PaymentRepository
from app.schemas.common import PageParams, SortParams


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.payments = PaymentRepository(session)

    async def list_payments(
        self, *, page_params: PageParams, sort_params: SortParams, order_id: uuid.UUID | None = None
    ) -> tuple[list[Payment], int]:
        query = self.payments.search_query(order_id=order_id)
        items, total = await self.payments.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_payment(self, payment_id: uuid.UUID) -> Payment:
        payment = await self.payments.get_by_id_with_transactions(payment_id)
        if payment is None:
            raise NotFoundError("Payment not found.")
        return payment
