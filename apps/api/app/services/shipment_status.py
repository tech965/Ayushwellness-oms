"""Unified 12-status shipment classification (OMS SOW Module 2).

The SOW's shipment-status funnel is wider than what this OMS persists:
`Shipment.current_status` (`ShipmentStatus`) has 9 values and an order
has no `Shipment` row at all until one is created. Rather than widen the
stored enum (a migration, and a backfill decision for every historical
row -- out of scope and explicitly not approved), the 12 SOW statuses are
DERIVED, read-only, from state that already exists:

  `Order.status` / `Order.fulfillment_status` (before a `Shipment`
  exists, or when one never will -- e.g. a Shopify-only fulfillment) and
  `Shipment.current_status` / `Shipment.rto_status` (once one exists).

Both a pure-Python classifier (for unit tests and small in-memory lists)
and an equivalent SQL `case()` builder (for aggregate queries over the
whole table) are provided below. They MUST stay in lock-step -- see
`tests/test_shipment_status_analytics.py`, which asserts both produce
the same bucket for every seeded combination.

## The 12 buckets, and exactly how each is reached

- `order_placed`: `Order.status == PENDING`. Order exists, not yet
  confirmed -- nothing shipping-related has happened yet.
- `ready_to_ship`: `Order.status` in (CONFIRMED, PROCESSING, PACKED) and
  no `Shipment` row exists yet (or every row the order has is CANCELLED,
  i.e. an abandoned shipment attempt -- treated as if none exists, since
  the order still needs a fresh one). Mirrors
  `OrderRepository.shipment_queue_query`'s own "awaiting shipment
  processing" rule, just split finer.
- `pickup_pending`: same order states as above, but a non-cancelled
  `Shipment` row exists with `current_status == PENDING` (AWB/booking
  created, courier hasn't scanned a pickup event yet).
- `picked_up` / `in_transit` / `out_for_delivery` / `delivered` /
  `ndr` / `cancelled`: the controlling `Shipment.current_status` maps
  1:1 -- these already exist as distinct `ShipmentStatus` values.
- `rto_initiated` / `rto_in_transit` / `rto_delivered`: `ShipmentStatus`
  only has `RTO_INITIATED`/`RTO_DELIVERED` -- it never gained an
  in-transit value because `Shipment.rto_status` (`RTOStatus`:
  INITIATED/IN_TRANSIT/RECEIVED/CANCELLED) already carries that finer
  state (see `app/integrations/shiprocket/sync.py`'s
  `RTO_STATUS_FROM_SHIPMENT_STATUS`, which sets it from the same tracking
  events). So: `current_status == RTO_INITIATED` and `rto_status ==
  IN_TRANSIT` -> `rto_in_transit`; `current_status == RTO_INITIATED`
  and anything else (`INITIATED`, `None`, or a courier reusing this
  status after a `CANCELLED` RTO attempt) -> `rto_initiated`;
  `current_status == RTO_DELIVERED` -> `rto_delivered` regardless of
  `rto_status` (`RTOStatus` has no "re-opened after received" value).
- A `Shipment` row with `current_status == CANCELLED` -> `cancelled`,
  same as `Order.status == CANCELLED` with no live shipment.

## Judgment calls flagged for review

1. **Order confirmed/processing/packed whose ONLY `Shipment` row is
   `CANCELLED`, with no reshipment yet** -> `cancelled`, the same direct
   mapping any other `CANCELLED` shipment gets. The "controlling
   shipment" picked by `OrderRepository.unified_status_base_query` is
   simply the most recently created one, cancelled or not -- it is NOT
   filtered out the way `shipment_queue_query`'s "awaiting shipment
   processing" rule filters it (that query answers a different question,
   "does this order still need a *fresh* booking", not "what is its
   current tracking status"). The moment a real reshipment happens, the
   new, non-cancelled `Shipment` row is newer and naturally becomes the
   controlling one again -- no special-casing needed either way.
2. **`Order.status` is SHIPPED or DELIVERED with no `Shipment` row at
   all** (a historical order fulfilled directly through Shopify, never
   routed through this OMS's own Shiprocket pipeline --
   `shipment_queue_query`'s docstring calls this out explicitly for the
   same reason it filters on `fulfillment_status`). Classified as
   `delivered` when `Order.fulfillment_status == FULFILLED` (Shopify's
   own record says it arrived), else folded into `ready_to_ship` as the
   least-wrong "needs attention" bucket, since this OMS has no shipment
   timeline for it at all. This is a pre-existing data-shape quirk, not
   something Module 2 introduces.
3. **An order with an active (non-cancelled) `Shipment` but
   `Order.status` still PENDING/CANCELLED** (should not happen in
   practice -- a `Shipment` implies confirmation) is classified purely
   from the `Shipment` side (whichever of picked_up/in_transit/etc. it
   is), since a live shipment is a stronger signal than a stale order
   status.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import Case, case

from app.models.enums import FulfillmentStatus, OrderStatus, RTOStatus, ShipmentStatus

UNIFIED_STATUSES: Final[tuple[str, ...]] = (
    "order_placed",
    "ready_to_ship",
    "pickup_pending",
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "rto_initiated",
    "rto_in_transit",
    "rto_delivered",
    "ndr",
    "cancelled",
)

_PRE_SHIPMENT_ORDER_STATUSES: Final[frozenset[str]] = frozenset(
    {OrderStatus.CONFIRMED.value, OrderStatus.PROCESSING.value, OrderStatus.PACKED.value}
)

# Every shipment status that maps 1:1 onto a unified bucket of the same
# literal name -- see the module docstring for the 3 that don't
# (`rto_initiated` needs `rto_status` too, and `pending` isn't one at
# all, it needs the order-level pre/no-shipment split).
_DIRECT_SHIPMENT_STATUS_MAP: Final[dict[str, str]] = {
    ShipmentStatus.PICKED_UP.value: "picked_up",
    ShipmentStatus.IN_TRANSIT.value: "in_transit",
    ShipmentStatus.OUT_FOR_DELIVERY.value: "out_for_delivery",
    ShipmentStatus.DELIVERED.value: "delivered",
    ShipmentStatus.NDR.value: "ndr",
    ShipmentStatus.CANCELLED.value: "cancelled",
    ShipmentStatus.RTO_DELIVERED.value: "rto_delivered",
}


def classify_unified_status(
    *,
    order_status: str,
    fulfillment_status: str,
    shipment_current_status: str | None,
    shipment_rto_status: str | None,
) -> str:
    """Pure function: the controlling shipment's state (`None` when the
    order has no non-cancelled `Shipment` row) plus the order's own
    status -> exactly one of `UNIFIED_STATUSES`. All inputs are plain
    strings (enum `.value`s) so this has zero DB/ORM dependency and is
    trivial to unit test with constructed fixtures.
    """
    if shipment_current_status is not None:
        direct = _DIRECT_SHIPMENT_STATUS_MAP.get(shipment_current_status)
        if direct is not None:
            return direct
        if shipment_current_status == ShipmentStatus.RTO_INITIATED.value:
            if shipment_rto_status == RTOStatus.IN_TRANSIT.value:
                return "rto_in_transit"
            return "rto_initiated"
        if shipment_current_status == ShipmentStatus.PENDING.value:
            return "pickup_pending"
        # Defensive: a future ShipmentStatus value not yet mapped above.
        return "ready_to_ship"

    if order_status == OrderStatus.CANCELLED.value:
        return "cancelled"
    if order_status == OrderStatus.PENDING.value:
        return "order_placed"
    if order_status in _PRE_SHIPMENT_ORDER_STATUSES:
        return "ready_to_ship"
    # SHIPPED/DELIVERED with no local Shipment row -- judgment call (2) above.
    if fulfillment_status == FulfillmentStatus.FULFILLED.value:
        return "delivered"
    return "ready_to_ship"


def unified_status_sql_case(
    *,
    order_status_col,
    fulfillment_status_col,
    shipment_current_status_col,
    shipment_rto_status_col,
) -> Case:
    """SQL mirror of `classify_unified_status`, for `GROUP BY` aggregate
    queries over the whole `orders`/`shipments` join -- see that
    function's docstring for the rules themselves; this must stay
    logically identical to it (enforced by
    `tests/test_shipment_status_analytics.py`).
    """
    return case(
        (
            shipment_current_status_col == ShipmentStatus.PICKED_UP.value,
            "picked_up",
        ),
        (
            shipment_current_status_col == ShipmentStatus.IN_TRANSIT.value,
            "in_transit",
        ),
        (
            shipment_current_status_col == ShipmentStatus.OUT_FOR_DELIVERY.value,
            "out_for_delivery",
        ),
        (
            shipment_current_status_col == ShipmentStatus.DELIVERED.value,
            "delivered",
        ),
        (
            shipment_current_status_col == ShipmentStatus.NDR.value,
            "ndr",
        ),
        (
            shipment_current_status_col == ShipmentStatus.CANCELLED.value,
            "cancelled",
        ),
        (
            shipment_current_status_col == ShipmentStatus.RTO_DELIVERED.value,
            "rto_delivered",
        ),
        (
            (shipment_current_status_col == ShipmentStatus.RTO_INITIATED.value)
            & (shipment_rto_status_col == RTOStatus.IN_TRANSIT.value),
            "rto_in_transit",
        ),
        (
            shipment_current_status_col == ShipmentStatus.RTO_INITIATED.value,
            "rto_initiated",
        ),
        (
            shipment_current_status_col == ShipmentStatus.PENDING.value,
            "pickup_pending",
        ),
        (
            shipment_current_status_col.isnot(None),
            "ready_to_ship",
        ),
        (
            order_status_col == OrderStatus.CANCELLED.value,
            "cancelled",
        ),
        (
            order_status_col == OrderStatus.PENDING.value,
            "order_placed",
        ),
        (
            order_status_col.in_(tuple(_PRE_SHIPMENT_ORDER_STATUSES)),
            "ready_to_ship",
        ),
        (
            fulfillment_status_col == FulfillmentStatus.FULFILLED.value,
            "delivered",
        ),
        else_="ready_to_ship",
    )
