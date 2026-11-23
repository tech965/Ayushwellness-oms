"""OMS SOW Module 3 -- Fulfilment & Delivery Performance Dashboard.

Section A (Internal Order Fulfilment) and Section B (Courier Delivery
Performance), computed purely from existing `orders`/`shipments` columns
via `OrderRepository.unified_status_base_query` (the same Order-LEFT-JOIN-
controlling-Shipment query Module 2 built) and the unified-status
classifier in `app.services.shipment_status`. No new tables, no
migration, no courier-API calls.

Not available in live-data mode, for the same reason Module 2's endpoints
aren't -- see `GET /shipments/status-analytics`'s
`unavailable_in_live_mode_for` dependency.

## Timestamp choices (SOW: "use the actual available business event
timestamps... do not invent timestamps or silently substitute dates")

- **Fulfilment clock start** = `Order.confirmed_at` when set, else
  `Order.order_datetime`. `confirmed_at` is `NULL` for any order that
  reached CONFIRMED a way other than the Telecaller-confirm flow (e.g.
  auto-confirmed on Shopify sync for a paid prepaid order -- see
  `Order.confirmed_at`'s own docstring) -- `order_datetime` is the correct
  fallback for those, not a gap to exclude. This also doubles as the
  clock `pending_fulfilment` orders' 24h/48h ageing is measured from.
- **Fulfilment clock end** = the controlling `Shipment.pickup_date`. An
  order only has one once a courier has actually scanned a pickup event
  (`Shiprocket` sync writes this -- never backfilled/guessed), so a
  `NULL` here for an order that HAS reached `picked_up` or later is a
  genuine data gap, counted in `missing_pickup_timestamp_count` and
  excluded from the average rather than imputed. A `NULL` for an order
  still in `order_placed`/`ready_to_ship`/`pickup_pending` is simply "not
  fulfilled yet" -- not a gap, excluded from the average for the ordinary
  reason (nothing to measure yet), counted instead under "pending
  fulfilment".
- **Delivery clock** = controlling `Shipment.pickup_date` ->
  `actual_delivery_date`, and ONLY for shipments whose `current_status`
  is `DELIVERED` -- per the SOW's own formula ("Total Successfully
  Delivered Orders"). A `CANCELLED` shipment never delivered (excluded by
  this filter automatically). An `RTO_INITIATED`/`RTO_DELIVERED` shipment
  went back to origin, not to the customer -- also excluded by this same
  filter, so RTO shipments cannot pollute "successfully delivered"
  averages, exactly as required. A `DELIVERED` shipment missing either
  timestamp is a genuine data gap (counted, excluded) -- same
  no-imputation rule as above.
- Both daily trends bucket by the IST calendar day of the EVENT itself
  (the day fulfilment completed / the day delivery completed), not the
  day the order was placed -- `app.core.timezone.to_ist`, the same
  helper Module 2's `unified_status_daily_counts` uses, for the same
  "this OMS's business day is IST, not UTC" reason.
- `ShipmentEvent` (the raw per-scan timeline) is deliberately NOT
  re-queried here: `Shipment.pickup_date`/`actual_delivery_date` are
  already the deduplicated, denormalized result of that timeline (see
  `ShipmentService.add_tracking_event`'s dedup-by-`external_event_id`/
  `(status, event_timestamp)` logic) -- re-deriving from raw events would
  just reimplement that dedup a second time for no benefit.

## Team / warehouse attribution -- NOT implemented, by design

A.5 ("fulfilment performance by team/warehouse") has no genuine data to
attribute from. There is no `warehouse_id`/`team_id`/`fulfilled_by`
column on `Order` or `Shipment`. The only "warehouse" model in this
codebase (`app.models.inventory_architecture.Warehouse`) is the IMS stock
ledger's warehouse -- it has no FK from `Order`/`Shipment` at all, so an
order cannot be attributed to one without inventing a mapping. The only
per-person attribution that exists (`Order.confirmed_by_telecaller_id`,
and `ShipmentService.get_analytics`'s `telecaller_stats`) is sales/
confirmation attribution, not warehouse/fulfilment-ops attribution --
using it here would silently misrepresent a Telecaller's sales metric as
a warehouse's operational one. Per the SOW's own rule ("Do not invent
team or warehouse attribution"), this is reported as unavailable
(`team_warehouse_attribution_available=False`, with a reason) rather than
faked.

## Geographical zone -- a documented judgment call, not authoritative
courier-zone data

The SOW's "zone" almost certainly means each courier's own billing/
transit zone (same-city/regional/national/metro, etc.) -- that
classification lives entirely inside the courier's own rate card/API and
isn't available in this codebase (no zone field on `Shipment`/`Courier`,
no Shiprocket zone data synced locally). What IS available is the
order's own normalized Indian state (`Order.shipping_address['state']`,
already normalized by the existing `normalize_state` helper in
`app.services.supply_intelligence_service`, reused here rather than
duplicated). State is used as the "zone" grouping -- a real, available
proxy, NOT the courier's actual billing zone. This is called out
explicitly in the response (`zone_is_state_based=True`) rather than
left implicit.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import to_ist
from app.models.enums import ShipmentStatus
from app.models.order import Order
from app.repositories.order import OrderRepository
from app.repositories.shipment_effective import (
    effective_courier_name,
    effective_delivery_date,
    effective_pickup_date,
    partner_display_name,
    partner_identity,
)
from app.services.shipment_status import classify_unified_status
from app.services.supply_intelligence_service import normalize_state

_PENDING_FULFILMENT_STATUSES = frozenset({"ready_to_ship", "pickup_pending"})

TEAM_WAREHOUSE_UNAVAILABLE_REASON = (
    "No warehouse/team attribution exists on Order or Shipment in the current data "
    "model -- the only Warehouse model (IMS stock ledger) has no link to orders or "
    "shipments, and the only per-person attribution (confirmed_by_telecaller_id) is "
    "sales/confirmation attribution, not fulfilment-ops attribution. Reported as "
    "unavailable rather than invented, per the SOW's own rule."
)


def _service_names(services: dict[str, int] | None) -> list[str]:
    """Original courier/service names, most used first."""
    if not services:
        return []
    return [name for name, _ in sorted(services.items(), key=lambda kv: (-kv[1], kv[0]))]


def _family_name(services: dict[str, int] | None, key: str | None) -> str:
    if key is None:
        return "Unassigned"
    names = _service_names(services)
    return partner_display_name(names[0]) if names else "Unknown"


def _avg(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


@dataclass
class _FulfilmentRow:
    fulfilment_start: datetime
    pickup_date: datetime | None
    unified_status: str
    has_shipment: bool = True


@dataclass
class _DeliveryRow:
    courier_id: uuid.UUID | None
    courier_name: str | None
    delivered: bool
    pickup_date: datetime | None
    actual_delivery_date: datetime | None
    payment_type: str
    state_raw: str | None


class FulfilmentPerformanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)

    async def _fulfilment_rows(
        self,
        *,
        courier_id: uuid.UUID | None,
        payment_type: str | None,
        sku: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> list[_FulfilmentRow]:
        base_stmt, active_shipment = self.orders.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        stmt = base_stmt.with_only_columns(
            Order.order_datetime,
            Order.confirmed_at,
            Order.status,
            Order.fulfillment_status,
            effective_pickup_date(active_shipment).label("pickup_date"),
            active_shipment.current_status,
            active_shipment.rto_status,
        )
        rows = (await self.session.execute(stmt)).all()
        out = []
        for order_dt, confirmed_at, order_status, fulfillment_status, pickup_date, shp_status, rto_status in rows:
            unified = classify_unified_status(
                order_status=order_status.value,
                fulfillment_status=fulfillment_status.value,
                shipment_current_status=shp_status.value if shp_status else None,
                shipment_rto_status=rto_status.value if rto_status else None,
            )
            out.append(
                _FulfilmentRow(
                    fulfilment_start=confirmed_at or order_dt,
                    pickup_date=pickup_date,
                    unified_status=unified,
                    has_shipment=shp_status is not None,
                )
            )
        return out

    async def get_fulfilment_summary(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, object]:
        rows = await self._fulfilment_rows(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        now = datetime.now(UTC)
        fulfilment_days: list[float] = []
        missing_pickup_timestamp_count = 0
        pending_count = 0
        pending_over_24h = 0
        pending_over_48h = 0

        for row in rows:
            if row.pickup_date is not None:
                days = (row.pickup_date - row.fulfilment_start).total_seconds() / 86400
                fulfilment_days.append(days)
            elif row.unified_status in _PENDING_FULFILMENT_STATUSES:
                pending_count += 1
                age_hours = (now - row.fulfilment_start).total_seconds() / 3600
                if age_hours > 24:
                    pending_over_24h += 1
                if age_hours > 48:
                    pending_over_48h += 1
            elif row.has_shipment and row.unified_status not in (
                "order_placed",
                "cancelled",
                "fulfilled_tracking_unavailable",
            ):
                # (No Shipment row at all -- e.g. fulfilled directly in
                # Shopify -- means no pickup timestamp by design, not a gap.)
                # Reached picked_up-or-later per the classifier, but the
                # controlling shipment has no `pickup_date` -- a genuine
                # upstream data gap (sync lag / legacy row), not "pending".
                missing_pickup_timestamp_count += 1

        return {
            "avg_fulfilment_days": _avg(fulfilment_days),
            "fulfilled_order_count": len(fulfilment_days),
            "missing_pickup_timestamp_count": missing_pickup_timestamp_count,
            "pending_fulfilment_count": pending_count,
            "pending_over_24h_count": pending_over_24h,
            "pending_over_48h_count": pending_over_48h,
            "team_warehouse_breakdown": [],
            "team_warehouse_attribution_available": False,
            "team_warehouse_attribution_note": TEAM_WAREHOUSE_UNAVAILABLE_REASON,
        }

    async def get_fulfilment_trend(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, object]:
        rows = await self._fulfilment_rows(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        buckets: dict[str, list[float]] = {}
        for row in rows:
            if row.pickup_date is None:
                continue
            day = to_ist(row.pickup_date).date().isoformat()
            days = (row.pickup_date - row.fulfilment_start).total_seconds() / 86400
            buckets.setdefault(day, []).append(days)
        daily = [
            {"date": day, "avg_fulfilment_days": _avg(values), "fulfilled_order_count": len(values)}
            for day, values in sorted(buckets.items())
        ]
        return {"daily": daily}

    async def _delivery_rows(
        self,
        *,
        courier_id: uuid.UUID | None,
        payment_type: str | None,
        sku: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> list[_DeliveryRow]:
        base_stmt, active_shipment = self.orders.unified_status_base_query(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        stmt = base_stmt.with_only_columns(
            active_shipment.courier_id,
            effective_courier_name(active_shipment).label("courier_name"),
            active_shipment.current_status,
            effective_pickup_date(active_shipment).label("pickup_date"),
            effective_delivery_date(active_shipment).label("actual_delivery_date"),
            Order.payment_type,
            Order.shipping_address,
        )
        rows = (await self.session.execute(stmt)).all()
        out = []
        for c_id, c_name, status, pickup_date, delivery_date, pay_type, shipping_address in rows:
            out.append(
                _DeliveryRow(
                    courier_id=c_id,
                    courier_name=c_name,
                    delivered=status is not None and status.value == ShipmentStatus.DELIVERED.value,
                    pickup_date=pickup_date,
                    actual_delivery_date=delivery_date,
                    payment_type=pay_type.value,
                    state_raw=(shipping_address or {}).get("state") if shipping_address else None,
                )
            )
        return out

    async def get_delivery_performance(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        zone: str | None = None,
    ) -> dict[str, object]:
        rows = await self._delivery_rows(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        zone_filter = normalize_state(zone) if zone else None

        overall_days: list[float] = []
        missing_timestamp_count = 0
        by_courier: dict[str | None, list[float]] = {}
        partner_ids: dict[str | None, set[uuid.UUID]] = {}
        partner_services: dict[str | None, dict[str, int]] = {}
        by_zone: dict[str, list[float]] = {}
        unmapped_zone_count = 0
        cod_days: list[float] = []
        prepaid_days: list[float] = []

        for row in rows:
            if not row.delivered:
                continue
            if row.pickup_date is None or row.actual_delivery_date is None:
                missing_timestamp_count += 1
                continue
            days = (row.actual_delivery_date - row.pickup_date).total_seconds() / 86400
            if days < 0:
                # Delivery stamped before pickup is corrupt data, not a
                # (negative) delivery time -- excluded and counted, never
                # averaged in.
                missing_timestamp_count += 1
                continue

            zone_name = normalize_state(row.state_raw)
            if zone_filter is not None and zone_name != zone_filter:
                continue

            overall_days.append(days)
            key = partner_identity(row.courier_name, row.courier_id)
            by_courier.setdefault(key, []).append(days)
            if key is not None:
                service = " ".join((row.courier_name or "").split())
                if service:
                    services = partner_services.setdefault(key, {})
                    services[service] = services.get(service, 0) + 1
                if row.courier_id is not None:
                    partner_ids.setdefault(key, set()).add(row.courier_id)
            if zone_name is not None:
                by_zone.setdefault(zone_name, []).append(days)
            else:
                unmapped_zone_count += 1
            if row.payment_type == "cod":
                cod_days.append(days)
            elif row.payment_type == "prepaid":
                prepaid_days.append(days)


        return {
            "overall_avg_delivery_days": _avg(overall_days),
            "delivered_order_count": len(overall_days),
            "missing_timestamp_count": missing_timestamp_count,
            "by_courier": [
                {
                    "courier_key": key,
                    # A carrier family can span several Courier rows; only
                    # expose an id when it is unambiguous.
                    "courier_id": next(iter(partner_ids[key]))
                    if len(partner_ids.get(key, ())) == 1
                    else None,
                    "courier_name": _family_name(partner_services.get(key), key),
                    "service_names": _service_names(partner_services.get(key)),
                    "avg_delivery_days": _avg(values),
                    "delivered_order_count": len(values),
                }
                for key, values in sorted(by_courier.items(), key=lambda kv: -len(kv[1]))
            ],
            "by_zone": [
                {"zone": zone_name, "avg_delivery_days": _avg(values), "delivered_order_count": len(values)}
                for zone_name, values in sorted(by_zone.items(), key=lambda kv: -len(kv[1]))
            ],
            "zone_is_state_based": True,
            "unmapped_zone_order_count": unmapped_zone_count,
            "cod_avg_delivery_days": _avg(cod_days),
            "cod_delivered_order_count": len(cod_days),
            "prepaid_avg_delivery_days": _avg(prepaid_days),
            "prepaid_delivered_order_count": len(prepaid_days),
        }

    async def get_delivery_trend(
        self,
        *,
        courier_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        sku: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, object]:
        rows = await self._delivery_rows(
            courier_id=courier_id,
            payment_type=payment_type,
            sku=sku,
            date_from=date_from,
            date_to=date_to,
        )
        buckets: dict[str, list[float]] = {}
        for row in rows:
            if not row.delivered or row.pickup_date is None or row.actual_delivery_date is None:
                continue
            days = (row.actual_delivery_date - row.pickup_date).total_seconds() / 86400
            if days < 0:
                continue
            day = to_ist(row.actual_delivery_date).date().isoformat()
            buckets.setdefault(day, []).append(days)
        daily = [
            {"date": day, "avg_delivery_days": _avg(values), "delivered_order_count": len(values)}
            for day, values in sorted(buckets.items())
        ]
        return {"daily": daily}
