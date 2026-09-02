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
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.enums import PaymentStatus
from app.models.payment import Payment
from app.repositories.payment import PaymentRepository
from app.schemas.analytics import StatusCount
from app.schemas.cashfree import (
    CashfreePaymentMethodBreakdownItem,
    CashfreePaymentMethodBreakdownResponse,
    CashfreePaymentOverviewResponse,
    CashfreePaymentTrendPoint,
    CashfreePaymentTrendResponse,
)
from app.schemas.common import PageParams, SortParams
from app.services.analytics_service import _bucket_key, _kpi, _previous_range, resolve_range
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

    # --- Payment analytics (dashboard cards/charts) ---------------------
    # Provider-agnostic sibling of `CashfreePaymentService`'s own
    # analytics (which is deliberately, permanently scoped to
    # `Payment.provider == "cashfree"` — see that file, untouched here).
    # `provider=None` means "every provider" (Shopify included), matching
    # exactly how `list_payments`/`PaymentRepository.search_query` already
    # treat an absent provider filter for the payments table these cards
    # sit above — so the table and the cards can never silently disagree
    # about what "no provider selected" means. Reuses the exact same
    # `CashfreePayment*Response` shapes (identical fields) rather than
    # duplicating them under a new name, since the frontend's existing
    # `PaymentOverviewCards`/`PaymentTrendChart`/`PaymentMethodBreakdown`
    # components already render that shape unchanged.

    async def _status_aggregates(
        self, date_from: datetime, date_to: datetime, provider: str | None
    ) -> dict[PaymentStatus, tuple[int, Decimal]]:
        conditions = [Payment.created_at >= date_from, Payment.created_at <= date_to]
        if provider:
            conditions.append(Payment.provider == provider)
        stmt = (
            select(Payment.status, func.count(), func.coalesce(func.sum(Payment.amount), 0))
            .where(*conditions)
            .group_by(Payment.status)
        )
        rows = (await self.session.execute(stmt)).all()
        return {status: (count, Decimal(amount)) for status, count, amount in rows}

    async def get_payment_overview(
        self, date_from: datetime | None, date_to: datetime | None, provider: str | None = None
    ) -> CashfreePaymentOverviewResponse:
        r = resolve_range(date_from, date_to)
        prev = _previous_range(r)
        current = await self._status_aggregates(r.date_from, r.date_to, provider)
        previous = await self._status_aggregates(prev.date_from, prev.date_to, provider)

        def count_of(agg: dict[PaymentStatus, tuple[int, Decimal]], status: PaymentStatus) -> int:
            return agg.get(status, (0, Decimal("0")))[0]

        def amount_of(
            agg: dict[PaymentStatus, tuple[int, Decimal]], status: PaymentStatus
        ) -> Decimal:
            return agg.get(status, (0, Decimal("0")))[1]

        def refunded_count(agg: dict[PaymentStatus, tuple[int, Decimal]]) -> int:
            return count_of(agg, PaymentStatus.REFUNDED) + count_of(
                agg, PaymentStatus.PARTIALLY_REFUNDED
            )

        total_current = sum((c for c, _ in current.values()), 0)
        total_previous = sum((c for c, _ in previous.values()), 0)

        return CashfreePaymentOverviewResponse(
            date_from=r.date_from,
            date_to=r.date_to,
            total_payments=_kpi(total_current, total_previous),
            paid_payments=_kpi(
                count_of(current, PaymentStatus.PAID), count_of(previous, PaymentStatus.PAID)
            ),
            pending_payments=_kpi(
                count_of(current, PaymentStatus.PENDING),
                count_of(previous, PaymentStatus.PENDING),
            ),
            failed_payments=_kpi(
                count_of(current, PaymentStatus.FAILED), count_of(previous, PaymentStatus.FAILED)
            ),
            refunded_payments=_kpi(refunded_count(current), refunded_count(previous)),
            total_amount=_kpi(
                amount_of(current, PaymentStatus.PAID), amount_of(previous, PaymentStatus.PAID)
            ),
            pending_amount=_kpi(
                amount_of(current, PaymentStatus.PENDING),
                amount_of(previous, PaymentStatus.PENDING),
            ),
            status_breakdown=[
                StatusCount(status=status.value, count=count)
                for status, (count, _amount) in current.items()
            ],
        )

    async def get_payment_trend(
        self,
        date_from: datetime | None,
        date_to: datetime | None,
        interval: str,
        provider: str | None = None,
    ) -> CashfreePaymentTrendResponse:
        r = resolve_range(date_from, date_to)
        conditions = [Payment.created_at >= r.date_from, Payment.created_at <= r.date_to]
        if provider:
            conditions.append(Payment.provider == provider)
        stmt = select(Payment.created_at, Payment.status, Payment.amount).where(*conditions)
        rows = (await self.session.execute(stmt)).all()

        buckets: dict[str, dict[str, Decimal | int]] = defaultdict(
            lambda: {
                "total_count": 0,
                "total_amount": Decimal("0"),
                "paid_count": 0,
                "paid_amount": Decimal("0"),
                "pending_count": 0,
                "failed_count": 0,
            }
        )
        for created_at, status, amount in rows:
            bucket = buckets[_bucket_key(created_at, interval)]
            bucket["total_count"] += 1
            bucket["total_amount"] += amount
            if status == PaymentStatus.PAID:
                bucket["paid_count"] += 1
                bucket["paid_amount"] += amount
            elif status == PaymentStatus.PENDING:
                bucket["pending_count"] += 1
            elif status == PaymentStatus.FAILED:
                bucket["failed_count"] += 1

        points = [
            CashfreePaymentTrendPoint(bucket=key, **values)
            for key, values in sorted(buckets.items())
        ]
        return CashfreePaymentTrendResponse(interval=interval, points=points)

    async def get_payment_method_breakdown(
        self, date_from: datetime | None, date_to: datetime | None, provider: str | None = None
    ) -> CashfreePaymentMethodBreakdownResponse:
        r = resolve_range(date_from, date_to)
        # Cashfree rows carry a granular method (upi/card/netbanking/...)
        # in `payment_metadata`; Shopify rows never populate that JSON
        # column at all (`OrderService.upsert_synced_order` never sets
        # it) but always have a real `payment_type` (cod/prepaid/other).
        # Falling back to it means "All providers"/"Shopify" never show
        # an empty breakdown just because the finer-grained field isn't
        # something Shopify's sync has ever populated — every row still
        # contributes its own real, non-fabricated value.
        method_expr = func.coalesce(
            Payment.payment_metadata["payment_method"].as_string(),
            cast(Payment.payment_type, String),
        )
        conditions = [
            Payment.created_at >= r.date_from,
            Payment.created_at <= r.date_to,
            method_expr.is_not(None),
        ]
        if provider:
            conditions.append(Payment.provider == provider)
        stmt = (
            select(method_expr, func.count(), func.coalesce(func.sum(Payment.amount), 0))
            .where(*conditions)
            .group_by(method_expr)
        )
        rows = (await self.session.execute(stmt)).all()
        return CashfreePaymentMethodBreakdownResponse(
            items=[
                CashfreePaymentMethodBreakdownItem(
                    payment_method=method, count=count, amount=Decimal(amount)
                )
                for method, count, amount in rows
            ]
        )
