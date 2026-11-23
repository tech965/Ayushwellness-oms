"""Order-level drilldowns behind the Module 3 (Fulfilment & Delivery) and
Module 4 (Shipping Partner) dashboards.

Every list here is built from the SAME base query, the SAME effective
pickup/delivery/courier values and the SAME inclusion rules as the
aggregates in `FulfilmentPerformanceService` and
`ShippingPartnerPerformanceService`, so the number on a KPI card, chart bar
or partner row always equals the length of the list it opens:

- Fulfilled / fulfilment time: an order with a pickup timestamp
  (`pickup_date - fulfilment_start`, start = `confirmed_at` else
  `order_datetime`).
- Delivery time: shipment `DELIVERED` with both timestamps and a
  non-negative pickup -> delivery duration. Missing timestamps are never
  inferred; such orders are simply not in these lists (the page reports how
  many are excluded).
- Pending: no pickup timestamp and unified status `ready_to_ship` /
  `pickup_pending`, aged from the fulfilment start.
- Partner lists: orders whose partner identity (carrier family) matches;
  orders with no identifiable courier are never listed.

One order is one row (the controlling shipment is a single LEFT JOINed row),
so multiple tracking events can never duplicate an order. Filtering and
paging happen after the same per-row rules, in memory, exactly like the
aggregates, over the already date/payment/SKU-filtered set.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import to_ist
from app.models.enums import ShipmentStatus
from app.models.order import Order, OrderItem
from app.repositories.order import OrderRepository
from app.repositories.shipment_effective import (
    effective_courier_name,
    effective_delivery_date,
    effective_pickup_date,
    partner_identity,
)
from app.schemas.common import PageParams
from app.services.shipment_status import classify_unified_status
from app.services.shipment_status_analytics_service import RTO_STATUSES, SHIPPED_OR_LATER_STATUSES

FULFILMENT_SCOPES = (
    "fulfilled",
    "delivered",
    "cod_delivered",
    "prepaid_delivered",
    "pending",
    "pending_24h",
    "pending_48h",
)
PARTNER_METRICS = (
    "allocated",
    "picked_up",
    "delivered",
    "rto",
    "cod_success",
    "prepaid_success",
)
_PENDING_STATUSES = frozenset({"ready_to_ship", "pickup_pending"})


@dataclass
class _Row:
    order_id: uuid.UUID
    order_number: str
    order_datetime: datetime
    payment_type: str
    unified_status: str
    shipment_status: str | None
    rto_status: str | None
    awb: str | None
    courier_id: uuid.UUID | None
    courier_name: str | None
    pickup_date: datetime | None
    delivery_date: datetime | None
    fulfilment_start: datetime

    @property
    def delivered(self) -> bool:
        return self.shipment_status == ShipmentStatus.DELIVERED.value

    @property
    def partner(self) -> str | None:
        return partner_identity(self.courier_name, self.courier_id)

    def fulfilment_days(self) -> float | None:
        if self.pickup_date is None:
            return None
        return (self.pickup_date - self.fulfilment_start).total_seconds() / 86400

    def delivery_days(self) -> float | None:
        """Pickup -> delivery for a successfully delivered shipment; `None`
        when a timestamp is missing or the pair is corrupt (delivery before
        pickup) -- the same exclusions as the delivery aggregates.
        """
        if not self.delivered or self.pickup_date is None or self.delivery_date is None:
            return None
        days = (self.delivery_date - self.pickup_date).total_seconds() / 86400
        return days if days >= 0 else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


class PerformanceOrdersService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)

    async def _load(
        self,
        *,
        courier_id: uuid.UUID | None,
        payment_type: str | None,
        sku: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> list[_Row]:
        base_stmt, shipment = self.orders.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        stmt = base_stmt.with_only_columns(
            Order.id,
            Order.order_number,
            Order.order_datetime,
            Order.confirmed_at,
            Order.payment_type,
            Order.status,
            Order.fulfillment_status,
            shipment.current_status,
            shipment.rto_status,
            shipment.awb,
            shipment.courier_id,
            effective_courier_name(shipment).label("courier_name"),
            effective_pickup_date(shipment).label("pickup_date"),
            effective_delivery_date(shipment).label("delivery_date"),
        )
        out: list[_Row] = []
        for (
            order_id,
            order_number,
            order_dt,
            confirmed_at,
            pay_type,
            order_status,
            fulfillment_status,
            shp_status,
            rto_status,
            awb,
            c_id,
            c_name,
            pickup,
            delivery,
        ) in (await self.session.execute(stmt)).all():
            unified = classify_unified_status(
                order_status=order_status.value,
                fulfillment_status=fulfillment_status.value,
                shipment_current_status=shp_status.value if shp_status else None,
                shipment_rto_status=rto_status.value if rto_status else None,
            )
            out.append(
                _Row(
                    order_id=order_id,
                    order_number=order_number,
                    order_datetime=order_dt,
                    payment_type=pay_type.value,
                    unified_status=unified,
                    shipment_status=shp_status.value if shp_status else None,
                    rto_status=rto_status.value if rto_status else None,
                    awb=awb,
                    courier_id=c_id,
                    courier_name=c_name,
                    pickup_date=pickup,
                    delivery_date=delivery,
                    fulfilment_start=confirmed_at or order_dt,
                )
            )
        return out

    async def _skus(self, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not order_ids:
            return {}
        rows = (
            await self.session.execute(
                select(OrderItem.order_id, OrderItem.sku).where(
                    OrderItem.order_id.in_(order_ids), OrderItem.sku.is_not(None)
                )
            )
        ).all()
        by_order: dict[uuid.UUID, list[str]] = {}
        for order_id, sku_value in rows:
            by_order.setdefault(order_id, []).append(sku_value)
        return {oid: ", ".join(skus) for oid, skus in by_order.items()}

    async def _page(
        self,
        rows: list[_Row],
        *,
        q: str | None,
        page_params: PageParams,
        now: datetime,
    ) -> tuple[list[dict[str, object]], int]:
        if q and q.strip():
            term = q.strip().lower()
            rows = [
                r
                for r in rows
                if term in r.order_number.lower() or (r.awb and term in r.awb.lower())
            ]
        rows = sorted(rows, key=lambda r: (r.order_datetime, str(r.order_id)), reverse=True)
        total = len(rows)
        page = rows[page_params.offset : page_params.offset + page_params.page_size]
        skus = await self._skus([r.order_id for r in page])
        results = []
        for r in page:
            pending = r.pickup_date is None and r.unified_status in _PENDING_STATUSES
            results.append(
                {
                    "order_id": r.order_id,
                    "order_number": r.order_number,
                    "order_datetime": r.order_datetime,
                    "payment_type": r.payment_type,
                    "sku_summary": skus.get(r.order_id),
                    "unified_status": r.unified_status,
                    "shipment_status": r.shipment_status,
                    "awb": r.awb,
                    "courier_name": r.courier_name,
                    "pickup_date": r.pickup_date,
                    "actual_delivery_date": r.delivery_date,
                    "fulfilment_days": _round(r.fulfilment_days()),
                    "delivery_days": _round(r.delivery_days()),
                    "pending_hours": _round(
                        (now - r.fulfilment_start).total_seconds() / 3600 if pending else None
                    ),
                }
            )
        return results, total

    async def fulfilment_orders(
        self,
        *,
        scope: str,
        page_params: PageParams,
        day: str | None = None,
        q: str | None = None,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        """`day` (IST `YYYY-MM-DD`) narrows `fulfilled` to orders picked up
        that day and the `*delivered` scopes to orders delivered that day --
        the same bucketing as the two daily trend charts.
        """
        rows = await self._load(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        now = datetime.now(UTC)
        selected: list[_Row] = []
        for r in rows:
            if scope == "fulfilled":
                if r.pickup_date is None:
                    continue
                if day and to_ist(r.pickup_date).date().isoformat() != day:
                    continue
            elif scope in ("delivered", "cod_delivered", "prepaid_delivered"):
                if r.delivery_days() is None:
                    continue
                if scope == "cod_delivered" and r.payment_type != "cod":
                    continue
                if scope == "prepaid_delivered" and r.payment_type != "prepaid":
                    continue
                if day and (
                    r.delivery_date is None or to_ist(r.delivery_date).date().isoformat() != day
                ):
                    continue
            else:  # pending, pending_24h, pending_48h
                if r.pickup_date is not None or r.unified_status not in _PENDING_STATUSES:
                    continue
                age_hours = (now - r.fulfilment_start).total_seconds() / 3600
                if scope == "pending_24h" and age_hours <= 24:
                    continue
                if scope == "pending_48h" and age_hours <= 48:
                    continue
            selected.append(r)
        return await self._page(selected, q=q, page_params=page_params, now=now)

    async def partner_orders(
        self,
        *,
        metric: str,
        page_params: PageParams,
        partner: str | None = None,
        q: str | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        """Orders behind a partner row / metric. `partner` is the response's
        `courier_key` (omit for every partner). Counting rules mirror
        `ShippingPartnerPerformanceService._partner_counts`: allocated = all;
        picked_up = picked up or later; delivered; rto; COD/Prepaid success
        = delivered orders of that payment mode (the numerator of the
        success rate, whose denominator is that mode's picked-up orders).
        """
        rows = await self._load(
            courier_id=None,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        selected: list[_Row] = []
        for r in rows:
            key = r.partner
            if key is None:
                continue
            if partner is not None and key != partner:
                continue
            shipped = r.unified_status in SHIPPED_OR_LATER_STATUSES
            delivered = r.unified_status == "delivered"
            if metric == "picked_up" and not shipped:
                continue
            if metric == "delivered" and not delivered:
                continue
            if metric == "rto" and r.unified_status not in RTO_STATUSES:
                continue
            if metric == "cod_success" and not (delivered and r.payment_type == "cod"):
                continue
            if metric == "prepaid_success" and not (delivered and r.payment_type == "prepaid"):
                continue
            selected.append(r)
        return await self._page(selected, q=q, page_params=page_params, now=datetime.now(UTC))
