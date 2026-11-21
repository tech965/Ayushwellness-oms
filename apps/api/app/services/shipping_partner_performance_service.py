"""OMS SOW Module 4 -- Shipping Partner Allocation & Performance Analysis.

Section A (Order Allocation Dashboard), Section B (Shipping Partner
Performance Comparison), and Section C (Decision-Making Dashboard) --
computed purely from existing `orders`/`shipments`/`couriers` columns via
`OrderRepository.unified_status_counts_by_courier` /
`fulfilment_to_delivery_rows` (both reusing Module 2's unified-status
classifier and `unified_status_base_query`) and Module 3's
`FulfilmentPerformanceService.get_delivery_performance` (reused directly
for the by-courier avg-delivery-days figure, rather than recomputed, so
Module 3 and Module 4's numbers for the same courier can never drift
apart). No new tables, no migration, no courier-API calls.

Not available in live-data mode, for the same reason Modules 2 & 3
aren't -- see `GET /shipments/status-analytics`'s
`unavailable_in_live_mode_for` dependency.

## Section C is not a separate computation

The SOW's "Decision-Making Dashboard" asks for the same six comparison
axes (delivery speed, delivery success rate, RTO %, shipping cost, COD
performance, Prepaid performance) that Section B's per-partner rows
already contain in full. Building a third backend aggregation of the
same numbers would only risk the two drifting apart; the single
`get_partner_performance` response is Section B and C's shared source,
and the frontend renders it as a sortable/comparable table for the
"decision-making" view.

## Counting unit: order-level, "most recent shipment wins" -- same as
Modules 2 & 3, not shipment-level

An order can have multiple `Shipment` rows (e.g. an RTO'd attempt
followed by a reshipment via a different courier/AWB). Module 4 counts
at the ORDER level using the same "controlling shipment = most recently
created one" rule `unified_status_base_query` already established for
Modules 2 & 3, rather than counting every individual shipment attempt
separately. This is a deliberate consistency choice, not an oversight:
Module 2's RTO %/Delivery % and Module 3's by-courier delivery-days for
a given courier would otherwise silently disagree with Module 4's
numbers for that same courier on the consolidated dashboard the SOW
asks for, even though they're describing the same underlying orders. The
trade-off: a reshipped order's original (cancelled/RTO'd) courier
attempt stops counting toward that first courier's allocation the moment
a newer shipment record exists for the order -- the order's allocation
"belongs" to whichever shipment is current, not to history. Flagged here
for review since the SOW's own phrasing ("Total orders allocated") could
also support shipment-level counting; order-level was chosen for
cross-module consistency.

## Shipping cost per delivered/RTO order (B.10/B.11) -- NOT implemented,
by design

No shipping-cost/invoice/billing field exists anywhere on `Shipment` or
`Courier`. The only cost-shaped column anywhere near this is
`Order.shipping_charge` -- confirmed (via
`app/integrations/shopify/normalizer.py`'s
`"shipping_charge": _money((raw.get("shippingLine") or
{}).get("originalPriceSet"))`) to be the CUSTOMER-FACING shipping fee
Shopify charged the buyer, not what the business actually paid the
courier for that shipment. Using it here would misrepresent a revenue
line as a cost line. Real per-shipment courier billing data is squarely
SOW Module 8's job ("Shipping Fees Reconciliation & Cost Analytics") and
has not been built. Reported as unavailable
(`shipping_cost_available=False`, with a reason), per the same "do not
invent" rule Module 3 applied to team/warehouse attribution.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courier import Courier
from app.repositories.order import OrderRepository
from app.services.fulfilment_performance_service import FulfilmentPerformanceService
from app.services.shipment_status_analytics_service import RTO_STATUSES, SHIPPED_OR_LATER_STATUSES

SHIPPING_COST_UNAVAILABLE_REASON = (
    "No shipping-cost/invoice field exists on Shipment or Courier. Order.shipping_charge is "
    "the customer-facing shipping fee Shopify charged the buyer (shippingLine.originalPriceSet), "
    "not the actual amount the business paid the courier -- using it here would misrepresent a "
    "revenue line as a cost line. Real per-shipment courier billing belongs to SOW Module 8 "
    "(Shipping Fees Reconciliation & Cost Analytics), which has not been built. Reported as "
    "unavailable rather than invented."
)


def _avg(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _pct(numerator: int, denominator: int) -> float | None:
    """`None` (not `0.0`) when there is no qualifying population at all --
    callers must render "not available", never a misleading 0%.
    """
    return round((numerator / denominator) * 100, 2) if denominator else None


@dataclass
class _PartnerCounts:
    allocated: int = 0
    shipped: int = 0
    delivered: int = 0
    rto: int = 0
    cod_allocated: int = 0
    cod_shipped: int = 0
    cod_delivered: int = 0
    prepaid_allocated: int = 0
    prepaid_shipped: int = 0
    prepaid_delivered: int = 0


@dataclass
class _FulfilmentToDeliveryAgg:
    days: list[float] = field(default_factory=list)
    missing_delivery_timestamp_count: int = 0


class ShippingPartnerPerformanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.fulfilment = FulfilmentPerformanceService(session)

    async def _partner_counts(
        self,
        *,
        courier_id: uuid.UUID | None,
        payment_type: str | None,
        sku: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> dict[uuid.UUID | None, _PartnerCounts]:
        rows = await self.orders.unified_status_counts_by_courier(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        by_courier: dict[uuid.UUID | None, _PartnerCounts] = {}
        for c_id, pay_type, status, count in rows:
            if c_id is None:
                # No shipment / no courier assigned yet -- not "allocated"
                # to any partner, so excluded from every partner's totals.
                continue
            agg = by_courier.setdefault(c_id, _PartnerCounts())
            agg.allocated += count
            is_shipped = status in SHIPPED_OR_LATER_STATUSES
            is_delivered = status == "delivered"
            is_rto = status in RTO_STATUSES
            if is_shipped:
                agg.shipped += count
            if is_delivered:
                agg.delivered += count
            if is_rto:
                agg.rto += count
            if pay_type == "cod":
                agg.cod_allocated += count
                if is_shipped:
                    agg.cod_shipped += count
                if is_delivered:
                    agg.cod_delivered += count
            elif pay_type == "prepaid":
                agg.prepaid_allocated += count
                if is_shipped:
                    agg.prepaid_shipped += count
                if is_delivered:
                    agg.prepaid_delivered += count
        return by_courier

    async def _fulfilment_to_delivery_by_courier(
        self,
        *,
        courier_id: uuid.UUID | None,
        payment_type: str | None,
        sku: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> dict[uuid.UUID | None, _FulfilmentToDeliveryAgg]:
        rows = await self.orders.fulfilment_to_delivery_rows(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        out: dict[uuid.UUID | None, _FulfilmentToDeliveryAgg] = {}
        for c_id, _pay_type, status, fulfilment_start, actual_delivery_date in rows:
            if c_id is None or status != "delivered":
                continue
            agg = out.setdefault(c_id, _FulfilmentToDeliveryAgg())
            if actual_delivery_date is None:
                agg.missing_delivery_timestamp_count += 1
                continue
            days = (actual_delivery_date - fulfilment_start).total_seconds() / 86400
            agg.days.append(days)
        return out

    async def _courier_names(self, courier_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not courier_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Courier.id, Courier.name).where(Courier.id.in_(courier_ids))
            )
        ).all()
        return dict(rows)

    @staticmethod
    def _allocation_percentages(allocations: dict[uuid.UUID, int]) -> dict[uuid.UUID, float]:
        """Rounds each partner's share to 2dp such that the rounded
        values sum to EXACTLY 100.00 (the SOW's own example table shows
        a `Total` row of `100%`) -- largest-remainder method: round every
        share down to 2dp first, then hand out the leftover hundredths
        (100.00 minus the sum of the floors) one at a time to the
        partners with the largest fractional remainder, highest first.
        With zero partners this returns `{}` and is never called that way
        by `get_partner_allocation` (which only runs it when there is at
        least one allocated order).
        """
        total = sum(allocations.values())
        if total == 0:
            return dict.fromkeys(allocations, 0.0)
        raw = {c_id: (count / total) * 100 for c_id, count in allocations.items()}
        floored = {c_id: int(pct * 100) for c_id, pct in raw.items()}
        remainder = 10000 - sum(floored.values())
        remainders = sorted(
            allocations.keys(), key=lambda c_id: raw[c_id] * 100 - floored[c_id], reverse=True
        )
        for c_id in remainders[:remainder]:
            floored[c_id] += 1
        return {c_id: floored[c_id] / 100 for c_id in allocations}

    async def get_partner_allocation(
        self,
        *,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, object]:
        counts = await self._partner_counts(
            courier_id=None,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        delivery_perf = await self.fulfilment.get_delivery_performance(
            payment_type=payment_type, sku=sku, date_from=date_from, date_to=date_to
        )
        avg_delivery_by_courier = {
            row["courier_id"]: row["avg_delivery_days"] for row in delivery_perf["by_courier"]
        }

        allocations = {c_id: agg.allocated for c_id, agg in counts.items()}
        total_allocated = sum(allocations.values())
        pcts = self._allocation_percentages(allocations) if total_allocated else {}
        names = await self._courier_names(list(counts.keys()))

        rows = [
            {
                "courier_id": c_id,
                "courier_name": names.get(c_id, "Unknown"),
                "orders_allocated": agg.allocated,
                "allocation_percent": pcts.get(c_id, 0.0),
                "avg_delivery_days": avg_delivery_by_courier.get(c_id),
            }
            for c_id, agg in sorted(counts.items(), key=lambda kv: -kv[1].allocated)
        ]
        return {
            "partners": rows,
            "total_orders_allocated": total_allocated,
            "total_allocation_percent": round(sum(r["allocation_percent"] for r in rows), 2)
            if rows
            else 0.0,
        }

    async def get_partner_performance(
        self,
        *,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, object]:
        counts = await self._partner_counts(
            courier_id=None,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        delivery_perf = await self.fulfilment.get_delivery_performance(
            payment_type=payment_type, sku=sku, date_from=date_from, date_to=date_to
        )
        avg_delivery_by_courier = {
            row["courier_id"]: row["avg_delivery_days"] for row in delivery_perf["by_courier"]
        }
        fulfilment_to_delivery = await self._fulfilment_to_delivery_by_courier(
            courier_id=None,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        names = await self._courier_names(list(counts.keys()))

        partners = []
        for c_id, agg in sorted(counts.items(), key=lambda kv: -kv[1].allocated):
            ftd = fulfilment_to_delivery.get(c_id)
            partners.append(
                {
                    "courier_id": c_id,
                    "courier_name": names.get(c_id, "Unknown"),
                    "total_allocated": agg.allocated,
                    "total_picked_up": agg.shipped,
                    "total_delivered": agg.delivered,
                    "delivery_success_rate": _pct(agg.delivered, agg.shipped),
                    "rto_percentage": _pct(agg.rto, agg.shipped),
                    "avg_delivery_days": avg_delivery_by_courier.get(c_id),
                    "avg_fulfilment_to_delivery_days": _avg(ftd.days) if ftd else None,
                    "missing_delivery_timestamp_count": ftd.missing_delivery_timestamp_count
                    if ftd
                    else 0,
                    "cod_delivery_success_rate": _pct(agg.cod_delivered, agg.cod_shipped),
                    "prepaid_delivery_success_rate": _pct(
                        agg.prepaid_delivered, agg.prepaid_shipped
                    ),
                    "avg_shipping_cost_per_delivered_order": None,
                    "avg_shipping_cost_per_rto_order": None,
                }
            )
        return {
            "partners": partners,
            "shipping_cost_available": False,
            "shipping_cost_unavailable_reason": SHIPPING_COST_UNAVAILABLE_REASON,
        }
