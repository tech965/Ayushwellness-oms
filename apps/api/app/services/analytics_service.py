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

Revenue/order drill-down definitions (Total Revenue/Total Orders ->
COD/Prepaid -> Paid/Pending), confirmed against the real schema, not
assumed: `Order.total_amount` is the order total used for every revenue
figure below; `Order.payment_type` (`cod`/`prepaid`/`other`) is the
COD-vs-Prepaid split; `Order.payment_status` (`pending`/`authorized`/
`paid`/`failed`/`refunded`/`partially_refunded`) is the paid-vs-pending
split — "paid" means exactly `payment_status == PAID`, "pending" means
every other status. Both are scoped by `Order.order_datetime`, same as
every other KPI in this file, so a single date range produces
consistent cards/charts/tables. No order is ever double-counted (each
order has exactly one `payment_type` and one `payment_status`, so
COD+Prepaid always sums to the total, and Paid+Pending always sums to
the total within a payment type). Cancelled orders are **included**,
matching the existing `total_revenue`/`cod_value`/`prepaid_value`
figures elsewhere in this file, which have never excluded them — this
preserves that existing rule rather than inventing a new one for just
the new drill-down views.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import to_ist
from app.models.courier import Courier
from app.models.customer import Customer
from app.models.enums import (
    FulfillmentStatus,
    NDRStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
    RefundStatus,
    ReturnStatus,
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
from app.repositories.customer import CustomerRepository
from app.schemas.analytics import (
    AnalyticsSummaryResponse,
    BreakdownsResponse,
    CourierPerformance,
    KPIValue,
    OrdersTimeseriesResponse,
    PaymentStatusBreakdownItem,
    PaymentStatusBreakdownResponse,
    PaymentStatusTimeseriesPoint,
    PaymentStatusTimeseriesResponse,
    RecentActivityResponse,
    RecentNdrRto,
    RecentOrder,
    RecentPayment,
    RecentShipment,
    RefundsSummary,
    ReturnsRefundsSummaryResponse,
    ReturnsSummary,
    RevenueTimeseriesPoint,
    RevenueTimeseriesResponse,
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
        self.customers = CustomerRepository(session)

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
            **{key: _kpi(current_counts[key], previous_counts[key]) for key in current_counts},
        )

    async def _summary_counts(self, r: DateRange) -> dict[str, Decimal]:
        # Perf (pre-demo audit): these 9 figures were previously 9 separate
        # round trips, each scanning the same `Order` rows in this date
        # range -- confirmed live as the single slowest piece of
        # `get_summary` (measured ~500ms locally against real data, well
        # above every sibling analytics endpoint). Collapsed into one
        # conditional-aggregation query with IDENTICAL per-metric WHERE
        # conditions to before -- `func.count(case(...))` only counts rows
        # where the condition is true (a false/NULL case branch is excluded
        # from COUNT, standard SQL, no dialect-specific behavior -- verified
        # against both Postgres and this suite's SQLite), so every result
        # is byte-for-byte the same as the original 9 queries. Order of the
        # `.where(...)` date-range filter is unchanged.
        def _count_if(condition):  # noqa: ANN001, ANN202
            return func.count(case((condition, 1)))

        def _sum_if(condition):  # noqa: ANN001, ANN202
            return func.coalesce(func.sum(case((condition, Order.total_amount))), 0)

        order_row = (
            await self.session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(Order.total_amount), 0),
                    _count_if(Order.fulfillment_status == FulfillmentStatus.FULFILLED),
                    _count_if(Order.fulfillment_status == FulfillmentStatus.UNFULFILLED),
                    _count_if(Order.payment_type == PaymentType.COD),
                    _count_if(Order.payment_type == PaymentType.PREPAID),
                    # `Order.status` (OMS-internal pack/ship workflow) is a
                    # DIFFERENT column from `Order.payment_status` — both
                    # enums happen to share the string "pending" for
                    # unrelated concepts. "Pending Orders" here means
                    # orders not yet confirmed/processed by ops
                    # (`OrderStatus.PENDING`), matching the same `status`
                    # field the Orders page's own "Order Status" filter and
                    # the dashboard's "Order Status" breakdown already use
                    # — not a payment-pending count, which is a separate,
                    # already-visible bucket in the existing Payment Status
                    # breakdown.
                    _count_if(Order.status == OrderStatus.PENDING),
                    _sum_if(Order.payment_type == PaymentType.COD),
                    _sum_if(Order.payment_type == PaymentType.PREPAID),
                ).where(Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to)
            )
        ).one()
        (
            total_orders,
            total_revenue,
            fulfilled_orders,
            unfulfilled_orders,
            cod_orders,
            prepaid_orders,
            pending_orders,
            cod_value,
            prepaid_value,
        ) = (Decimal(v) if v is not None else Decimal("0") for v in order_row)

        # Perf (dashboard load-time audit): these 7 figures span 5
        # DIFFERENT tables (Customer, Product, Shipment x2 distinct WHERE
        # columns, NDR, RTO, Return, Refund), so they can't be collapsed
        # into one conditional-aggregation query the way the same-table
        # Order cluster above was — but they CAN be collapsed into one
        # ROUND TRIP: each becomes its own uncorrelated scalar subquery
        # (`SELECT (SELECT count(*) FROM customers WHERE ...), (SELECT
        # count(*) FROM products WHERE ...), ...` — no shared FROM, each
        # subquery independently planned/executed by Postgres), selected
        # together in one outer statement. Every subquery's WHERE is
        # byte-for-byte identical to the separate query it replaces, so
        # every result is identical too — only the number of client<->DB
        # round trips changes (was 7, now 1). `repeat_customers` just below
        # is intentionally NOT folded in here: it's a join against a
        # GROUP BY/HAVING subquery (`_repeat_order_counts_subquery`), not a
        # plain filtered count, and is already index-backed and cheap (see
        # `CustomerRepository.count_repeat_customers_in_range`) — folding a
        # GROUP BY into a scalar-subquery slot risks subtly changing its
        # semantics for no measurable benefit.
        def _scalar_count(model, *conditions):  # noqa: ANN001, ANN002, ANN202
            return select(func.count()).select_from(model).where(*conditions).scalar_subquery()

        other_row = (
            await self.session.execute(
                select(
                    _scalar_count(
                        Customer,
                        Customer.created_at >= r.date_from,
                        Customer.created_at <= r.date_to,
                    ),
                    _scalar_count(
                        Product, Product.created_at >= r.date_from, Product.created_at <= r.date_to
                    ),
                    _scalar_count(
                        Shipment,
                        Shipment.actual_delivery_date >= r.date_from,
                        Shipment.actual_delivery_date <= r.date_to,
                    ),
                    select(_count_if(Shipment.current_status == ShipmentStatus.IN_TRANSIT))
                    .where(Shipment.updated_at >= r.date_from, Shipment.updated_at <= r.date_to)
                    .scalar_subquery(),
                    select(_count_if(Shipment.current_status == ShipmentStatus.OUT_FOR_DELIVERY))
                    .where(Shipment.updated_at >= r.date_from, Shipment.updated_at <= r.date_to)
                    .scalar_subquery(),
                    select(_count_if(Shipment.delay_status == ShipmentDelayStatus.DELAYED))
                    .where(Shipment.updated_at >= r.date_from, Shipment.updated_at <= r.date_to)
                    .scalar_subquery(),
                    _scalar_count(
                        NDR,
                        NDR.created_at >= r.date_from,
                        NDR.created_at <= r.date_to,
                        NDR.status == NDRStatus.OPEN,
                    ),
                    _scalar_count(
                        RTO,
                        RTO.created_at >= r.date_from,
                        RTO.created_at <= r.date_to,
                        RTO.status.in_([RTOStatus.INITIATED, RTOStatus.IN_TRANSIT]),
                    ),
                    _scalar_count(
                        Return, Return.created_at >= r.date_from, Return.created_at <= r.date_to
                    ),
                    _scalar_count(
                        Refund, Refund.created_at >= r.date_from, Refund.created_at <= r.date_to
                    ),
                )
            )
        ).one()
        (
            total_customers,
            total_products,
            delivered_shipments,
            in_transit_shipments,
            out_for_delivery_shipments,
            delayed_shipments,
            open_ndr,
            open_rto,
            returns,
            refunds,
        ) = (Decimal(v) if v is not None else Decimal("0") for v in other_row)

        # Same `Customer.created_at` scope as `total_customers` above
        # (never a second date-filtering rule) -- see
        # `CustomerRepository.count_repeat_customers_in_range`'s docstring.
        repeat_customers = Decimal(
            await self.customers.count_repeat_customers_in_range(
                date_from=r.date_from, date_to=r.date_to
            )
        )

        return {
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "total_customers": total_customers,
            "repeat_customers": repeat_customers,
            "total_products": total_products,
            "fulfilled_orders": fulfilled_orders,
            "unfulfilled_orders": unfulfilled_orders,
            "cod_orders": cod_orders,
            "prepaid_orders": prepaid_orders,
            "pending_orders": pending_orders,
            "cod_value": cod_value,
            "prepaid_value": prepaid_value,
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

    async def get_payment_status_breakdown(
        self,
        date_from: datetime | None,
        date_to: datetime | None,
        payment_type: PaymentType | None,
    ) -> PaymentStatusBreakdownResponse:
        """Paid-vs-pending snapshot (counts + revenue), optionally scoped
        to one `payment_type` — the data behind the COD/Prepaid Revenue
        and COD/Prepaid Orders drill-down cards+donut. See this module's
        docstring for the exact paid/pending rule.
        """
        r = resolve_range(date_from, date_to)
        conditions = [Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to]
        if payment_type is not None:
            conditions.append(Order.payment_type == payment_type)

        stmt = (
            select(
                Order.payment_status,
                func.count(),
                func.coalesce(func.sum(Order.total_amount), 0),
            )
            .where(*conditions)
            .group_by(Order.payment_status)
        )
        rows = (await self.session.execute(stmt)).all()

        items = [
            PaymentStatusBreakdownItem(status=str(status), count=cnt, revenue=Decimal(revenue))
            for status, cnt, revenue in rows
        ]
        paid = next((i for i in items if i.status == PaymentStatus.PAID.value), None)
        paid_count = paid.count if paid else 0
        paid_revenue = paid.revenue if paid else Decimal("0")
        total_count = sum(i.count for i in items)
        total_revenue = sum((i.revenue for i in items), Decimal("0"))

        return PaymentStatusBreakdownResponse(
            payment_type=payment_type.value if payment_type else None,
            total_count=total_count,
            total_revenue=total_revenue,
            paid_count=paid_count,
            paid_revenue=paid_revenue,
            pending_count=total_count - paid_count,
            pending_revenue=total_revenue - paid_revenue,
            items=items,
        )

    async def get_revenue_timeseries(
        self, date_from: datetime | None, date_to: datetime | None, interval: str
    ) -> RevenueTimeseriesResponse:
        """COD-vs-Prepaid orders/revenue per date bucket — drives the
        Revenue Analytics timeline chart (COD/Prepaid/Total lines) and the
        Total Orders drill-down timeline chart (COD/Prepaid/Total bars),
        both from the same query so the two views can never disagree.
        """
        r = resolve_range(date_from, date_to)
        stmt = select(Order.order_datetime, Order.payment_type, Order.total_amount).where(
            Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to
        )
        rows = (await self.session.execute(stmt)).all()

        buckets: dict[str, dict[str, Decimal | int]] = defaultdict(
            lambda: {
                "cod_orders": 0,
                "cod_revenue": Decimal("0"),
                "prepaid_orders": 0,
                "prepaid_revenue": Decimal("0"),
            }
        )
        for order_datetime, payment_type, total_amount in rows:
            bucket = buckets[_bucket_key(order_datetime, interval)]
            if payment_type == PaymentType.COD:
                bucket["cod_orders"] += 1
                bucket["cod_revenue"] += total_amount
            elif payment_type == PaymentType.PREPAID:
                bucket["prepaid_orders"] += 1
                bucket["prepaid_revenue"] += total_amount
            # PaymentType.OTHER is intentionally excluded from both -- it
            # already isn't part of cod_orders/prepaid_orders in
            # get_summary either, so total_orders/total_revenue here stay
            # consistent with "cod + prepaid" the same way the existing
            # summary KPIs do, rather than silently inventing a third
            # bucket this drill-down didn't ask for.

        points = [
            RevenueTimeseriesPoint(
                bucket=key,
                cod_orders=b["cod_orders"],
                cod_revenue=b["cod_revenue"],
                prepaid_orders=b["prepaid_orders"],
                prepaid_revenue=b["prepaid_revenue"],
                total_orders=b["cod_orders"] + b["prepaid_orders"],
                total_revenue=b["cod_revenue"] + b["prepaid_revenue"],
            )
            for key, b in sorted(buckets.items())
        ]
        return RevenueTimeseriesResponse(interval=interval, points=points)

    async def get_payment_status_timeseries(
        self,
        date_from: datetime | None,
        date_to: datetime | None,
        interval: str,
        payment_type: PaymentType,
    ) -> PaymentStatusTimeseriesResponse:
        """Paid-vs-pending orders/revenue per date bucket, within one
        payment type -- the COD/Prepaid Revenue and COD/Prepaid Orders
        drill-downs' own timeline charts.
        """
        r = resolve_range(date_from, date_to)
        stmt = select(Order.order_datetime, Order.payment_status, Order.total_amount).where(
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
            Order.payment_type == payment_type,
        )
        rows = (await self.session.execute(stmt)).all()

        buckets: dict[str, dict[str, Decimal | int]] = defaultdict(
            lambda: {
                "paid_orders": 0,
                "paid_revenue": Decimal("0"),
                "pending_orders": 0,
                "pending_revenue": Decimal("0"),
            }
        )
        for order_datetime, payment_status, total_amount in rows:
            bucket = buckets[_bucket_key(order_datetime, interval)]
            if payment_status == PaymentStatus.PAID:
                bucket["paid_orders"] += 1
                bucket["paid_revenue"] += total_amount
            else:
                bucket["pending_orders"] += 1
                bucket["pending_revenue"] += total_amount

        points = [
            PaymentStatusTimeseriesPoint(
                bucket=key,
                paid_orders=b["paid_orders"],
                paid_revenue=b["paid_revenue"],
                pending_orders=b["pending_orders"],
                pending_revenue=b["pending_revenue"],
                total_orders=b["paid_orders"] + b["pending_orders"],
                total_revenue=b["paid_revenue"] + b["pending_revenue"],
            )
            for key, b in sorted(buckets.items())
        ]
        return PaymentStatusTimeseriesResponse(
            interval=interval, payment_type=payment_type.value, points=points
        )

    async def get_breakdowns(
        self, date_from: datetime | None, date_to: datetime | None
    ) -> BreakdownsResponse:
        r = resolve_range(date_from, date_to)

        # Perf: `order_status`/`payment_type`/`payment_status`/
        # `fulfillment_status` were 4 separate `GROUP BY` round trips
        # against the SAME table and SAME `order_datetime` WHERE, each
        # differing only by which single column it grouped on. A `GROUP BY`
        # can't group on 4 different columns into 4 independent result
        # sets in one SQL statement without a dialect-specific feature
        # (`GROUPING SETS`, unsupported by this suite's SQLite) -- so
        # instead this fetches the 4 raw columns for every order in range
        # ONCE (same portability tradeoff `get_orders_timeseries` already
        # makes for this exact table/date-range shape) and tallies each
        # dimension in Python. Every (status, count) pair is identical to
        # before -- the original queries had no `ORDER BY`, so row order
        # was never guaranteed either.
        order_rows = (
            await self.session.execute(
                select(
                    Order.status,
                    Order.payment_type,
                    Order.payment_status,
                    Order.fulfillment_status,
                ).where(Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to)
            )
        ).all()
        order_status_counts: dict[str, int] = defaultdict(int)
        payment_type_counts: dict[str, int] = defaultdict(int)
        payment_status_counts: dict[str, int] = defaultdict(int)
        fulfillment_status_counts: dict[str, int] = defaultdict(int)
        for status, payment_type_val, payment_status_val, fulfillment_status_val in order_rows:
            order_status_counts[str(status)] += 1
            payment_type_counts[str(payment_type_val)] += 1
            payment_status_counts[str(payment_status_val)] += 1
            fulfillment_status_counts[str(fulfillment_status_val)] += 1

        def _to_status_counts(counts: dict[str, int]) -> list[StatusCount]:
            return [StatusCount(status=status, count=count) for status, count in counts.items()]

        shipment_status = await self._group_count(
            Shipment.current_status,
            Shipment.updated_at >= r.date_from,
            Shipment.updated_at <= r.date_to,
        )

        return BreakdownsResponse(
            order_status=_to_status_counts(order_status_counts),
            payment_type=_to_status_counts(payment_type_counts),
            payment_status=_to_status_counts(payment_status_counts),
            fulfillment_status=_to_status_counts(fulfillment_status_counts),
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
        in_transit_statuses = [ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT]
        in_transit_case = case((Shipment.current_status.in_(in_transit_statuses), 1), else_=0)
        pending_case = case((Shipment.current_status == ShipmentStatus.PENDING, 1), else_=0)
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
                func.sum(cast(in_transit_case, Numeric)),
                func.sum(cast(pending_case, Numeric)),
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
        for courier_id, name, shipment_count, delivered, in_transit, pending, ndr, rto in rows:
            shipment_count = shipment_count or 0
            delivered = int(delivered or 0)
            in_transit = int(in_transit or 0)
            pending = int(pending or 0)
            ndr = int(ndr or 0)
            rto = int(rto or 0)
            results.append(
                CourierPerformance(
                    courier_id=courier_id,
                    name=name,
                    shipment_count=shipment_count,
                    delivered_count=delivered,
                    in_transit_count=in_transit,
                    pending_count=pending,
                    ndr_count=ndr,
                    rto_count=rto,
                    delivered_pct=(delivered / shipment_count * 100) if shipment_count else 0.0,
                    ndr_pct=(ndr / shipment_count * 100) if shipment_count else 0.0,
                    rto_pct=(rto / shipment_count * 100) if shipment_count else 0.0,
                )
            )
        return results

    async def get_returns_refunds_summary(
        self, date_from: datetime | None, date_to: datetime | None
    ) -> ReturnsRefundsSummaryResponse:
        """Backs the dashboard's Returns/Refunds cards. `pending` for a
        `Return` means still in its request/receipt workflow (every status
        except its two terminal ones, `COMPLETED`/`CANCELLED`) -- mirrors
        how `open_ndr`/`open_rto` above define "open" as "not yet in a
        terminal state" rather than listing every non-terminal status by
        hand. `return_rate_pct` is against orders placed in the same
        window (matches every other rate-style figure here being
        period-scoped) -- `None` (never fabricated as 0%) when the period
        has no orders at all.
        """
        r = resolve_range(date_from, date_to)

        def _count_if(condition):  # noqa: ANN001, ANN202
            return func.count(case((condition, 1)))

        # Perf: same conditional-aggregation collapse as `_summary_counts`'
        # Order cluster -- `total_returns`/`completed_returns`/
        # `cancelled_returns` were 3 separate round trips against the SAME
        # `Return` rows in this date range (identical WHERE base, only the
        # extra status condition differed). Every result below is
        # byte-for-byte the same as the 3 queries it replaces.
        returns_row = (
            await self.session.execute(
                select(
                    func.count(),
                    _count_if(Return.status == ReturnStatus.COMPLETED),
                    _count_if(Return.status == ReturnStatus.CANCELLED),
                ).where(Return.created_at >= r.date_from, Return.created_at <= r.date_to)
            )
        ).one()
        total_returns, completed_returns, cancelled_returns = (
            int(v) if v is not None else 0 for v in returns_row
        )
        pending_returns = total_returns - completed_returns - cancelled_returns
        total_orders = await self._count_where(
            Order, Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to
        )
        return_rate_pct = (
            float(total_returns / total_orders * 100) if total_orders else None
        )

        # Same collapse for the 4 `Refund` metrics -- was 4 round trips
        # against the SAME `Refund` rows in this date range, now 1.
        refunds_row = (
            await self.session.execute(
                select(
                    func.count(),
                    _count_if(Refund.status == RefundStatus.COMPLETED),
                    _count_if(Refund.status.in_([RefundStatus.PENDING, RefundStatus.PROCESSING])),
                    func.coalesce(
                        func.sum(case((Refund.status == RefundStatus.COMPLETED, Refund.amount))), 0
                    ),
                ).where(Refund.created_at >= r.date_from, Refund.created_at <= r.date_to)
            )
        ).one()
        total_refunds, completed_refunds, pending_refunds, total_refund_amount_raw = refunds_row
        total_refunds = int(total_refunds) if total_refunds is not None else 0
        completed_refunds = int(completed_refunds) if completed_refunds is not None else 0
        pending_refunds = int(pending_refunds) if pending_refunds is not None else 0
        total_refund_amount = (
            Decimal(total_refund_amount_raw)
            if total_refund_amount_raw is not None
            else Decimal("0")
        )

        return ReturnsRefundsSummaryResponse(
            returns=ReturnsSummary(
                total_returns=int(total_returns),
                pending_returns=int(pending_returns),
                completed_returns=int(completed_returns),
                return_rate_pct=return_rate_pct,
            ),
            refunds=RefundsSummary(
                total_refunds=int(total_refunds),
                total_refund_amount=total_refund_amount,
                pending_refunds=int(pending_refunds),
                completed_refunds=int(completed_refunds),
            ),
        )

    async def get_recent_activity(self, limit: int = 5) -> RecentActivityResponse:
        # Perf: was `select(Order)`/`select(Shipment)`/etc. -- every
        # mapped column of the full entity (including large/irrelevant
        # ones like `Order.raw_external_payload`), for rows only ever read
        # for the 5 fields each response item actually has. Selecting just
        # those columns returns plain `Row` tuples instead of ORM entities
        # -- less data over the wire, no entity/identity-map construction
        # for rows never touched again -- while `ORDER BY ... LIMIT` is
        # unchanged (still index-backed now by `ix_orders_created_at`/
        # `ix_shipments_updated_at`/`ix_ndrs_created_at`/`ix_rtos_created_at`/
        # `ix_payments_created_at`), so the returned rows and their order
        # are identical to before.
        orders_stmt = (
            select(Order.id, Order.order_number, Order.total_amount, Order.status, Order.created_at)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        orders = (await self.session.execute(orders_stmt)).all()

        shipments_stmt = (
            select(
                Shipment.id,
                Shipment.order_id,
                Shipment.awb,
                Shipment.current_status,
                Shipment.updated_at,
            )
            .order_by(Shipment.updated_at.desc())
            .limit(limit)
        )
        shipments = (await self.session.execute(shipments_stmt)).all()

        ndrs_stmt = (
            select(NDR.id, NDR.order_id, NDR.status, NDR.reason, NDR.created_at)
            .order_by(NDR.created_at.desc())
            .limit(limit)
        )
        ndrs = (await self.session.execute(ndrs_stmt)).all()

        rtos_stmt = (
            select(RTO.id, RTO.order_id, RTO.status, RTO.reason, RTO.created_at)
            .order_by(RTO.created_at.desc())
            .limit(limit)
        )
        rtos = (await self.session.execute(rtos_stmt)).all()

        payments_stmt = (
            select(Payment.id, Payment.order_id, Payment.amount, Payment.status, Payment.created_at)
            .order_by(Payment.created_at.desc())
            .limit(limit)
        )
        payments = (await self.session.execute(payments_stmt)).all()

        ndr_rto = [
            RecentNdrRto(
                id=n_id,
                order_id=n_order_id,
                kind="ndr",
                status=n_status.value,
                reason=n_reason,
                created_at=n_created_at,
            )
            for n_id, n_order_id, n_status, n_reason, n_created_at in ndrs
        ] + [
            RecentNdrRto(
                id=r_id,
                order_id=r_order_id,
                kind="rto",
                status=r_status.value,
                reason=r_reason,
                created_at=r_created_at,
            )
            for r_id, r_order_id, r_status, r_reason, r_created_at in rtos
        ]
        ndr_rto.sort(key=lambda e: e.created_at, reverse=True)

        return RecentActivityResponse(
            recent_orders=[
                RecentOrder(
                    id=o_id,
                    order_number=o_order_number,
                    total_amount=o_total_amount,
                    status=o_status.value,
                    created_at=o_created_at,
                )
                for o_id, o_order_number, o_total_amount, o_status, o_created_at in orders
            ],
            recent_shipments=[
                RecentShipment(
                    id=s_id,
                    order_id=s_order_id,
                    awb=s_awb,
                    current_status=s_current_status.value,
                    updated_at=s_updated_at,
                )
                for s_id, s_order_id, s_awb, s_current_status, s_updated_at in shipments
            ],
            recent_ndr_rto=ndr_rto[:limit],
            recent_payments=[
                RecentPayment(
                    id=p_id,
                    order_id=p_order_id,
                    amount=p_amount,
                    status=p_status.value,
                    created_at=p_created_at,
                )
                for p_id, p_order_id, p_amount, p_status, p_created_at in payments
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
#
# `to_ist` lives in `app.core.timezone` (shared with the Telecalling
# follow-up "today/overdue/upcoming" filtering) so this bucketing and that
# filtering can never independently drift back into the bug above.
def _bucket_key(value: datetime, interval: str) -> str:
    ist_value = to_ist(value)
    if interval == "hour":
        return ist_value.strftime("%Y-%m-%dT%H:00")
    if interval == "week":
        start_of_week = ist_value.date() - timedelta(days=ist_value.weekday())
        return start_of_week.isoformat()
    if interval == "month":
        return ist_value.date().replace(day=1).isoformat()
    return ist_value.date().isoformat()
