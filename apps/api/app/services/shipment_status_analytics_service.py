"""OMS SOW Module 2 — Shipment Tracking & Status Analytics.

Unified 12-status breakdown, the SOW's 5 KPI percentages, date-wise
history, per-status order drill-down, and an Excel export — all computed
from the existing `orders`/`shipments` tables via
`OrderRepository.unified_status_*` and the pure classifier in
`app.services.shipment_status`. No new tables, no migration, no
courier-API calls: this reads what Shiprocket's existing webhook/sync
pipeline (`app.integrations.shiprocket`) has already written locally.

Not available in live-data mode (`settings.SHOPIFY_LIVE_MODE`) for the
same reason `ShipmentService.get_analytics`/`get_summary` aren't — see
`GET /shipments/analytics`'s `unavailable_in_live_mode_for` dependency.
In live mode, local `orders`/`shipments` rows aren't the source of
truth, so an aggregate over them would be silently wrong rather than
merely incomplete.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.repositories.order import OrderRepository
from app.repositories.shipment_effective import (
    effective_courier_name,
    effective_delivery_date,
    effective_pickup_date,
)
from app.schemas.common import PageParams
from app.services.shipment_status import UNIFIED_STATUSES, classify_unified_status

# Buckets that only exist once a courier has actually scanned a pickup —
# the SOW's "Total Shipped Orders" denominator for In-Transit/OFD/
# Delivery/RTO %. NDR and the RTO buckets are included: none of them can
# be reached without the shipment having been picked up first.
SHIPPED_OR_LATER_STATUSES = frozenset(
    {
        "picked_up",
        "in_transit",
        "out_for_delivery",
        "delivered",
        "rto_initiated",
        "rto_in_transit",
        "rto_delivered",
        "ndr",
    }
)

# "Eligible for pickup" = already confirmed, i.e. every bucket except the
# three that sit outside this OMS's shipping pipeline entirely (not yet
# confirmed, cancelled, or fulfilled in Shopify with no local shipment/
# tracking -- the last can never be picked up in this system) — the SOW's
# "Total Eligible Orders" denominator for Pickup %.
_NOT_ELIGIBLE_STATUSES = frozenset({"order_placed", "cancelled", "fulfilled_tracking_unavailable"})

RTO_STATUSES = frozenset({"rto_initiated", "rto_in_transit", "rto_delivered"})


# Management-friendly categories over the 13 technical statuses, in display
# order. Every unified status belongs to exactly one category, so category
# counts always add up to `total_orders`. Defined once here so the headline
# cards, the breakdown, the chart and every drilldown agree.
STATUS_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("new_orders", "New Orders", ("order_placed",)),
    ("ready_for_shipping", "Ready for Shipping", ("ready_to_ship",)),
    ("awaiting_pickup", "Awaiting Courier Pickup", ("pickup_pending",)),
    ("picked_up", "Picked Up by Courier", ("picked_up",)),
    ("in_transit", "In Transit", ("in_transit", "rto_in_transit")),
    ("out_for_delivery", "Out for Delivery", ("out_for_delivery",)),
    ("delivered", "Successfully Delivered", ("delivered",)),
    ("return_to_origin", "Return to Origin", ("rto_initiated", "rto_delivered")),
    ("delivery_issue", "Delivery Issue", ("ndr",)),
    ("cancelled", "Cancelled Orders", ("cancelled",)),
    ("tracking_pending", "Fulfilled – Tracking Pending", ("fulfilled_tracking_unavailable",)),
)
_GROUP_STATUSES = {key: statuses for key, _label, statuses in STATUS_GROUPS}
STATUS_GROUP_KEYS = tuple(_GROUP_STATUSES)

# Headline KPI -> the unified statuses it counts ("total" = every order).
# Fulfilled = handed to a courier (picked up or any later state) plus orders
# already marked fulfilled in Shopify that have no local tracking.
FULFILLED_STATUSES = SHIPPED_OR_LATER_STATUSES | {"fulfilled_tracking_unavailable"}
KPI_STATUSES: dict[str, frozenset[str] | None] = {
    "total": None,
    "fulfilled": frozenset(FULFILLED_STATUSES),
    "delivered": frozenset({"delivered"}),
    "rto": frozenset(RTO_STATUSES),
}
KPI_KEYS = tuple(KPI_STATUSES)


def _pct(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


class ShipmentStatusAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)

    async def get_status_analytics(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, object]:
        counts = await self.orders.unified_status_counts(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        total_orders = sum(counts.values())
        total_eligible = sum(
            c for status, c in counts.items() if status not in _NOT_ELIGIBLE_STATUSES
        )
        total_shipped = sum(
            c for status, c in counts.items() if status in SHIPPED_OR_LATER_STATUSES
        )
        rto_count = sum(counts[status] for status in RTO_STATUSES)

        return {
            "total_orders": total_orders,
            "total_eligible_orders": total_eligible,
            "total_shipped_orders": total_shipped,
            "total_fulfilled": sum(c for st, c in counts.items() if st in FULFILLED_STATUSES),
            "total_delivered": counts["delivered"],
            "total_rto": rto_count,
            "status_groups": [
                {
                    "key": key,
                    "label": label,
                    "statuses": list(statuses),
                    "count": sum(counts[st] for st in statuses),
                    "percent_of_total": _pct(sum(counts[st] for st in statuses), total_orders),
                }
                for key, label, statuses in STATUS_GROUPS
            ],
            "status_breakdown": [
                {
                    "status": status,
                    "count": counts[status],
                    "percent_of_total": _pct(counts[status], total_orders),
                }
                for status in UNIFIED_STATUSES
            ],
            "pickup_rate": _pct(counts["picked_up"], total_eligible),
            "in_transit_rate": _pct(counts["in_transit"], total_shipped),
            "out_for_delivery_rate": _pct(counts["out_for_delivery"], total_shipped),
            "delivery_rate": _pct(counts["delivered"], total_shipped),
            "rto_rate": _pct(rto_count, total_shipped),
        }

    async def get_status_history(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
    ) -> dict[str, object]:
        daily = await self.orders.unified_status_daily_counts(
            date_from=date_from,
            date_to=date_to,
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
        )
        return {"daily": daily}

    async def get_status_drilldown(
        self,
        *,
        page_params: PageParams,
        unified_status: str | None = None,
        group: str | None = None,
        kpi: str | None = None,
        q: str | None = None,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        """Orders behind one headline KPI (`kpi`), one management category
        (`group`) or one exact unified status (`unified_status`) -- same
        filters and same join as the counts, so a drilldown total always
        equals the number on the card/row that opened it. Each order
        appears once (the controlling shipment is a single LEFT JOINed row).
        """
        statuses: list[str] | None = None
        if group is not None:
            statuses = list(_GROUP_STATUSES[group])
        elif kpi is not None:
            kpi_set = KPI_STATUSES[kpi]
            statuses = None if kpi_set is None else [s for s in UNIFIED_STATUSES if s in kpi_set]
        stmt, active_shipment = self.orders.unified_status_drilldown_query(
            unified_status=unified_status,
            unified_statuses=statuses if unified_status is None else None,
            courier_id=courier_id,
            payment_type=payment_type,
            fulfillment_status=fulfillment_status,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        if q and q.strip():
            term = f"%{q.strip()}%"
            stmt = stmt.where(or_(Order.order_number.ilike(term), active_shipment.awb.ilike(term)))
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        paged = (
            stmt.add_columns(
                effective_courier_name(active_shipment).label("courier_name"),
                effective_pickup_date(active_shipment).label("pickup_date"),
                effective_delivery_date(active_shipment).label("delivery_date"),
            )
            .order_by(Order.order_datetime.desc(), Order.id)
            .offset(page_params.offset)
            .limit(page_params.page_size)
        )
        rows = (await self.session.execute(paged)).all()

        results = []
        for order, shipment, courier_name, pickup_date, delivery_date in rows:
            sku_summary = ", ".join(i.sku for i in order.items if i.sku) or None
            results.append(
                {
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "order_datetime": order.order_datetime,
                    "customer_name": order.customer.full_name if order.customer else None,
                    "payment_type": order.payment_type,
                    "sku_summary": sku_summary,
                    "total_amount": order.total_amount,
                    "unified_status": classify_unified_status(
                        order_status=order.status.value,
                        fulfillment_status=order.fulfillment_status.value,
                        shipment_current_status=(
                            shipment.current_status.value if shipment else None
                        ),
                        shipment_rto_status=(
                            shipment.rto_status.value if shipment and shipment.rto_status else None
                        ),
                    ),
                    "shipment_id": shipment.id if shipment else None,
                    "shipment_status": shipment.current_status.value if shipment else None,
                    "awb": shipment.awb if shipment else None,
                    "courier_name": courier_name,
                    "current_location": shipment.current_location if shipment else None,
                    "last_tracking_update_at": (
                        shipment.last_tracking_update_at if shipment else None
                    ),
                    "expected_delivery_date": shipment.expected_delivery_date if shipment else None,
                    "pickup_date": pickup_date,
                    "actual_delivery_date": delivery_date,
                }
            )
        return results, total or 0

    async def export_status_history_xlsx(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
    ) -> bytes:
        history = await self.get_status_history(
            date_from=date_from,
            date_to=date_to,
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
        )
        wb = Workbook()
        ws = wb.active
        ws.title = "Shipment Status History"
        ws.append(["Date", *UNIFIED_STATUSES])
        for point in history["daily"]:
            ws.append([point["date"], *[point[status] for status in UNIFIED_STATUSES]])

        buffer = BytesIO()
        wb.save(buffer)
        return buffer.getvalue()
