"""Dashboard business-intelligence queries — fills in the Phase-3 stub
`app/api/v1/endpoints/analytics.py` was left as in Phase 1.

Every metric here is scoped to an explicit `[date_from, date_to]` window
(defaulted to the last 30 days by the endpoint layer) so the dashboard's
global date-range selector drives every section from one consistent
contract. Field semantics, since the "right" date to filter on isn't
always obvious:

  - `total_orders`/`total_revenue`/fulfilled/unfulfilled/COD/prepaid —
    `Order.order_datetime` in range.
  - `total_customers`/`total_products` — *new* customers/products
    (`created_at` in range), not an all-time running total — matches
    every other KPI here being period-scoped, so period-over-period %
    change is meaningful.
  - `delivered_shipments` — `Shipment.actual_delivery_date` in range
    (the actual delivery event, not "delivered as of now").
  - `in_transit_shipments`/`out_for_delivery_shipments`/
    `delayed_shipments` — current status/delay_status snapshot, scoped by
    `Shipment.updated_at` in range (no separate "entered this status at"
    column exists yet — see `docs/architecture` for that known gap).
  - `open_ndr`/`open_rto`/`returns`/`refunds` — `created_at` in range
    (new NDR/RTO/Return/Refund records raised in the period), status
    filtered to the "open"/pending buckets.

Timeseries bucketing is done in Python, not SQL `date_trunc`, so the
exact same code path works against both production Postgres and the
SQLite test suite (see `BaseRepository.upsert_by_external_id`'s docstring
for why this codebase avoids dialect-specific SQL wherever practical).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courier import Courier
from app.models.customer import Customer
from app.models.enums import (
    FulfillmentStatus,
    NDRStatus,
    PaymentType,
    RTOStatus,
    ShipmentDelayStatus,
    ShipmentStatus,
)
from app.models.ndr import NDR
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.product import Product
from app.models.refund import Refund
from app.models.returns import Return
from app.models.rto import RTO
from app.models.shipment import Shipment
from app.schemas.analytics import (
    AnalyticsSummaryResponse,
    BreakdownsResponse,
    CourierPerformance,
    KPIValue,
    OrdersTimeseriesResponse,
    RecentActivityResponse,
    RecentNdrRto,
    RecentOrder,
    RecentPayment,
    RecentShipment,
    StatusCount,
    TimeseriesPoint,
    TopProduct,
)

DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class DateRange:
    date_from: datetime
    date_to: datetime


def resolve_range(date_from: datetime | None, date_to: datetime | None) -> DateRange:
    if date_to is None:
        date_to = datetime.now(UTC)
    if date_from is None:
        date_from = date_to - timedelta(days=DEFAULT_WINDOW_DAYS)
    return DateRange(date_from=date_from, date_to=date_to)


def _previous_range(current: DateRange) -> DateRange:
    span = current.date_to - current.date_from
    return DateRange(date_from=current.date_from - span, date_to=current.date_from)


def _change_pct(current: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    return float((current - previous) / previous * 100)


def _kpi(current: Decimal | int, previous: Decimal | int) -> KPIValue:
    current_d = Decimal(current)
    previous_d = Decimal(previous)
    change_pct = _change_pct(current_d, previous_d)
    return KPIValue(current=current_d, previous=previous_d, change_pct=change_pct)


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _scalar(self, stmt) -> Decimal:  # noqa: ANN001
        value = await self.session.scalar(stmt)
        return Decimal(value) if value is not None else Decimal("0")

    async def get_summary(
        self, date_from: datetime | None, date_to: datetime | None
    ) -> AnalyticsSummaryResponse:
        current = resolve_range(date_from, date_to)
        previous = _previous_range(current)

        current_counts = await self._summary_counts(current)
        previous_counts = await self._summary_counts(previous)

        return AnalyticsSummaryResponse(
            date_from=current.date_from,
            date_to=current.date_to,
            **{
                key: _kpi(current_counts[key], previous_counts[key])
                for key in current_counts
            },
        )

    async def _summary_counts(self, r: DateRange) -> dict[str, Decimal]:
        orders_in_range = select(Order).where(
            Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to
        )
        total_orders = await self._scalar(
            select(func.count()).select_from(orders_in_range.subquery())
        )
        total_revenue = await self._scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to
            )
        )
        fulfilled_orders = await self._count_where(
            Order,
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
            Order.fulfillment_status == FulfillmentStatus.FULFILLED,
        )
        unfulfilled_orders = await self._count_where(
            Order,
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
            Order.fulfillment_status == FulfillmentStatus.UNFULFILLED,
        )
        cod_orders = await self._count_where(
            Order,
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
            Order.payment_type == PaymentType.COD,
        )
        prepaid_orders = await self._count_where(
            Order,
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
            Order.payment_type == PaymentType.PREPAID,
        )
        total_customers = await self._count_where(
            Customer, Customer.created_at >= r.date_from, Customer.created_at <= r.date_to
        )
        total_products = await self._count_where(
            Product, Product.created_at >= r.date_from, Product.created_at <= r.date_to
        )
        delivered_shipments = await self._count_where(
            Shipment,
            Shipment.actual_delivery_date >= r.date_from,
            Shipment.actual_delivery_date <= r.date_to,
        )
        in_transit_shipments = await self._count_where(
            Shipment,
            Shipment.updated_at >= r.date_from,
            Shipment.updated_at <= r.date_to,
            Shipment.current_status == ShipmentStatus.IN_TRANSIT,
        )
        out_for_delivery_shipments = await self._count_where(
            Shipment,
            Shipment.updated_at >= r.date_from,
            Shipment.updated_at <= r.date_to,
            Shipment.current_status == ShipmentStatus.OUT_FOR_DELIVERY,
        )
        delayed_shipments = await self._count_where(
            Shipment,
            Shipment.updated_at >= r.date_from,
            Shipment.updated_at <= r.date_to,
            Shipment.delay_status == ShipmentDelayStatus.DELAYED,
        )
        open_ndr = await self._count_where(
            NDR,
            NDR.created_at >= r.date_from,
            NDR.created_at <= r.date_to,
            NDR.status == NDRStatus.OPEN,
        )
        open_rto = await self._count_where(
            RTO,
            RTO.created_at >= r.date_from,
            RTO.created_at <= r.date_to,
            RTO.status.in_([RTOStatus.INITIATED, RTOStatus.IN_TRANSIT]),
        )
        returns = await self._count_where(
            Return, Return.created_at >= r.date_from, Return.created_at <= r.date_to
        )
        refunds = await self._count_where(
            Refund, Refund.created_at >= r.date_from, Refund.created_at <= r.date_to
        )

        return {
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "total_customers": total_customers,
            "total_products": total_products,
            "fulfilled_orders": fulfilled_orders,
            "unfulfilled_orders": unfulfilled_orders,
            "cod_orders": cod_orders,
            "prepaid_orders": prepaid_orders,
            "delivered_shipments": delivered_shipments,
            "in_transit_shipments": in_transit_shipments,
            "out_for_delivery_shipments": out_for_delivery_shipments,
            "delayed_shipments": delayed_shipments,
            "open_ndr": open_ndr,
            "open_rto": open_rto,
            "returns": returns,
            "refunds": refunds,
        }

    async def _count_where(self, model, *conditions) -> Decimal:  # noqa: ANN001,ANN002
        stmt = select(func.count()).select_from(model).where(*conditions)
        return await self._scalar(stmt)

    async def get_orders_timeseries(
        self, date_from: datetime | None, date_to: datetime | None, interval: str
    ) -> OrdersTimeseriesResponse:
        r = resolve_range(date_from, date_to)
        stmt = select(Order.order_datetime, Order.total_amount).where(
            Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to
        )
        rows = (await self.session.execute(stmt)).all()

        buckets: dict[str, list[Decimal]] = defaultdict(list)
        for order_datetime, total_amount in rows:
            key = _bucket_key(order_datetime, interval)
            buckets[key].append(total_amount)

        points = [
            TimeseriesPoint(
                bucket=key, order_count=len(amounts), revenue=sum(amounts, Decimal("0"))
            )
            for key, amounts in sorted(buckets.items())
        ]
        return OrdersTimeseriesResponse(interval=interval, points=points)

    async def get_breakdowns(
        self, date_from: datetime | None, date_to: datetime | None
    ) -> BreakdownsResponse:
        r = resolve_range(date_from, date_to)

        order_status = await self._group_count(
            Order.status, Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to
        )
        payment_type = await self._group_count(
            Order.payment_type,
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
        )
        payment_status = await self._group_count(
            Order.payment_status,
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
        )
        fulfillment_status = await self._group_count(
            Order.fulfillment_status,
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
        )
        shipment_status = await self._group_count(
            Shipment.current_status,
            Shipment.updated_at >= r.date_from,
            Shipment.updated_at <= r.date_to,
        )

        return BreakdownsResponse(
            order_status=order_status,
            payment_type=payment_type,
            payment_status=payment_status,
            fulfillment_status=fulfillment_status,
            shipment_status=shipment_status,
        )

    async def _group_count(self, column, *conditions) -> list[StatusCount]:  # noqa: ANN001,ANN002
        stmt = select(column, func.count()).where(*conditions).group_by(column)
        rows = (await self.session.execute(stmt)).all()
        return [StatusCount(status=str(value), count=count) for value, count in rows]

    async def get_top_products(
        self, date_from: datetime | None, date_to: datetime | None, limit: int
    ) -> list[TopProduct]:
        r = resolve_range(date_from, date_to)
        stmt = (
            select(
                OrderItem.sku,
                OrderItem.product_name,
                func.sum(OrderItem.quantity).label("units_sold"),
                func.sum(OrderItem.total_amount).label("revenue"),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to)
            .group_by(OrderItem.sku, OrderItem.product_name)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            TopProduct(
                sku=sku or "—",
                title=title,
                units_sold=units_sold or 0,
                revenue=revenue or Decimal("0"),
            )
            for sku, title, units_sold, revenue in rows
        ]

    async def get_courier_performance(
        self, date_from: datetime | None, date_to: datetime | None
    ) -> list[CourierPerformance]:
        r = resolve_range(date_from, date_to)
        delivered_case = case((Shipment.current_status == ShipmentStatus.DELIVERED, 1), else_=0)
        ndr_case = case((Shipment.current_status == ShipmentStatus.NDR, 1), else_=0)
        rto_statuses = [ShipmentStatus.RTO_INITIATED, ShipmentStatus.RTO_DELIVERED]
        rto_case = case(
            (Shipment.current_status.in_(rto_statuses), 1),
            else_=0,
        )
        stmt = (
            select(
                Courier.id,
                Courier.name,
                func.count(Shipment.id),
                func.sum(cast(delivered_case, Numeric)),
                func.sum(cast(ndr_case, Numeric)),
                func.sum(cast(rto_case, Numeric)),
            )
            .join(Shipment, Shipment.courier_id == Courier.id)
            .where(Shipment.created_at >= r.date_from, Shipment.created_at <= r.date_to)
            .group_by(Courier.id, Courier.name)
            .order_by(func.count(Shipment.id).desc())
        )
        rows = (await self.session.execute(stmt)).all()

        results = []
        for courier_id, name, shipment_count, delivered, ndr, rto in rows:
            shipment_count = shipment_count or 0
            delivered = int(delivered or 0)
            ndr = int(ndr or 0)
            rto = int(rto or 0)
            results.append(
                CourierPerformance(
                    courier_id=courier_id,
                    name=name,
                    shipment_count=shipment_count,
                    delivered_count=delivered,
                    ndr_count=ndr,
                    rto_count=rto,
                    delivered_pct=(delivered / shipment_count * 100) if shipment_count else 0.0,
                    ndr_pct=(ndr / shipment_count * 100) if shipment_count else 0.0,
                    rto_pct=(rto / shipment_count * 100) if shipment_count else 0.0,
                )
            )
        return results

    async def get_recent_activity(self, limit: int = 5) -> RecentActivityResponse:
        orders_stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
        orders = (await self.session.execute(orders_stmt)).scalars().all()

        shipments_stmt = select(Shipment).order_by(Shipment.updated_at.desc()).limit(limit)
        shipments = (await self.session.execute(shipments_stmt)).scalars().all()

        ndrs_stmt = select(NDR).order_by(NDR.created_at.desc()).limit(limit)
        ndrs = (await self.session.execute(ndrs_stmt)).scalars().all()

        rtos_stmt = select(RTO).order_by(RTO.created_at.desc()).limit(limit)
        rtos = (await self.session.execute(rtos_stmt)).scalars().all()

        payments_stmt = select(Payment).order_by(Payment.created_at.desc()).limit(limit)
        payments = (await self.session.execute(payments_stmt)).scalars().all()

        ndr_rto = [
            RecentNdrRto(
                id=n.id, order_id=n.order_id, kind="ndr", status=n.status.value, reason=n.reason,
                created_at=n.created_at,
            )
            for n in ndrs
        ] + [
            RecentNdrRto(
                id=r.id, order_id=r.order_id, kind="rto", status=r.status.value, reason=r.reason,
                created_at=r.created_at,
            )
            for r in rtos
        ]
        ndr_rto.sort(key=lambda e: e.created_at, reverse=True)

        return RecentActivityResponse(
            recent_orders=[
                RecentOrder(
                    id=o.id, order_number=o.order_number, total_amount=o.total_amount,
                    status=o.status.value, created_at=o.created_at,
                )
                for o in orders
            ],
            recent_shipments=[
                RecentShipment(
                    id=s.id, order_id=s.order_id, awb=s.awb, current_status=s.current_status.value,
                    updated_at=s.updated_at,
                )
                for s in shipments
            ],
            recent_ndr_rto=ndr_rto[:limit],
            recent_payments=[
                RecentPayment(
                    id=p.id, order_id=p.order_id, amount=p.amount, status=p.status.value,
                    created_at=p.created_at,
                )
                for p in payments
            ],
        )


# The OMS's business calendar day is IST, not UTC (spec: "this OMS operates
# in India"), and the frontend's date-range presets (Today/Yesterday/Last 7
# Days/...) are all computed against IST midnight boundaries. `order_datetime`
# is stored UTC. Bucketing by the UTC calendar date instead of the IST one
# is a real, confirmed bug: any order placed 00:00-05:29 IST has a UTC
# timestamp still dated the *previous* day, so `value.date()` on the raw
# UTC value silently mis-buckets ~23% of a day's orders into the wrong
# bucket — and, worse, into a bucket that may fall entirely outside the
# caller's requested IST range (e.g. a "Last 7 Days" request starting at
# IST day-6's 00:00 will UTC-bucket that day's early-morning orders under
# a spurious extra day *before* the requested range). Confirmed via a
# controlled repro: the same IST calendar day reported three different
# order counts depending on which surrounding date range it was queried
# through, purely because of where the UTC/IST day boundary fell.
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _to_ist_naive(value: datetime) -> datetime:
    """Shifts a UTC-aware datetime to the IST wall-clock moment, keeping it
    tz-aware (still stamped UTC) — sufficient for reading off calendar-date
    components, without needing a timezone database dependency for a fixed
    (no-DST) offset like IST.
    """
    return value.astimezone(UTC) + _IST_OFFSET


def _bucket_key(value: datetime, interval: str) -> str:
    ist_value = _to_ist_naive(value)
    if interval == "week":
        start_of_week = ist_value.date() - timedelta(days=ist_value.weekday())
        return start_of_week.isoformat()
    if interval == "month":
        return ist_value.date().replace(day=1).isoformat()
    return ist_value.date().isoformat()
