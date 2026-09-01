"""Read-only — payment rows are created by `OrderService.create_order`
(a manually-recorded COD/prepaid payment) and by
`app.services.cashfree_payment_service.CashfreePaymentService` (a
Cashfree checkout session). This service never writes; it's the shared
read path both the generic `/payments` endpoints and the Cashfree
payments dashboard (`app.api.v1.endpoints.cashfree_payments`) sit on top
of, so a payment created either way shows up here identically.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.payment import Payment
from app.repositories.payment import PaymentRepository
from app.schemas.common import PageParams, SortParams
from app.services.export_service import ExportService


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.payments = PaymentRepository(session)

    async def list_payments(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        order_id: uuid.UUID | None = None,
        provider: str | None = None,
        status: str | None = None,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        payment_method: str | None = None,
    ) -> tuple[list[Payment], int]:
        """`order_id` alone remains fully backward compatible — every new
        filter defaults to `None` (no-op), matching
        `PaymentRepository.search_query`.
        """
        query = self.payments.search_query(
            order_id=order_id,
            provider=provider,
            status=status,
            q=q,
            date_from=date_from,
            date_to=date_to,
            payment_method=payment_method,
        )
        items, total = await self.payments.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_payment(self, payment_id: uuid.UUID) -> Payment:
        payment = await self.payments.get_by_id_with_transactions(payment_id)
        if payment is None:
            raise NotFoundError("Payment not found.")
        return payment

    async def export_payments(self, filters: dict) -> bytes:
        """Same filters as `list_payments`, unpaginated, capped at
        `ExportService.MAX_ROWS` — mirrors `OrderService.export_orders`.
        """
        query = self.payments.search_query(**filters)
        payments = await self.payments.list_for_export(query, limit=ExportService.MAX_ROWS)
        return ExportService().payments_to_xlsx(payments)
