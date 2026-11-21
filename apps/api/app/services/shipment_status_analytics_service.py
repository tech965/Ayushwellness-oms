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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.repositories.order import OrderRepository
from app.schemas.common import PageParams
from app.services.shipment_status import UNIFIED_STATUSES

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
# two that sit outside the shipping pipeline entirely — the SOW's
# "Total Eligible Orders" denominator for Pickup %.
_NOT_ELIGIBLE_STATUSES = frozenset({"order_placed", "cancelled"})

RTO_STATUSES = frozenset({"rto_initiated", "rto_in_transit", "rto_delivered"})


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
        total_shipped = sum(c for status, c in counts.items() if status in SHIPPED_OR_LATER_STATUSES)
        rto_count = sum(counts[status] for status in RTO_STATUSES)

        return {
            "total_orders": total_orders,
            "total_eligible_orders": total_eligible,
            "total_shipped_orders": total_shipped,
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
        unified_status: str,
        page_params: PageParams,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        stmt, _active_shipment = self.orders.unified_status_drilldown_query(
            unified_status=unified_status,
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        paged = (
            stmt.order_by(Order.order_datetime.desc())
            .offset(page_params.offset)
            .limit(page_params.page_size)
        )
        rows = (await self.session.execute(paged)).all()

        results = []
        for order, shipment in rows:
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
                    "unified_status": unified_status,
                    "shipment_id": shipment.id if shipment else None,
                    "awb": shipment.awb if shipment else None,
                    "courier_name": shipment.courier.name if shipment and shipment.courier else None,
                    "expected_delivery_date": shipment.expected_delivery_date if shipment else None,
                    "actual_delivery_date": shipment.actual_delivery_date if shipment else None,
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
