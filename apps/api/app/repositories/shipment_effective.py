"""Read-side "effective" pickup / delivery / courier for a shipment.

Why this exists
---------------
Tracking ingestion (`ShipmentEvent` rows + `Shipment.current_status`) has
always worked, but until now nothing ever copied the pickup / delivery
event times onto `Shipment.pickup_date` / `Shipment.actual_delivery_date`,
and nothing set `Shipment.courier_id` for shipments that arrive through
Shiprocket webhooks / pull-sync (they carry only a courier *name*, on the
event). Analytics that read only the denormalised columns therefore saw
every shipment as "no pickup, no delivery, no courier".

The truthful values are already stored, in the append-only
`shipment_events` timeline. These helpers read them from there WHEN the
denormalised column is NULL. Nothing is invented: a shipment with no
matching event keeps a NULL, exactly as before.

Event statuses are the raw courier strings, so they are matched with the
same case/whitespace-insensitive keys `normalize_shipment_status` uses
(`PICKED UP`/`SHIPPED` -> picked up, `DELIVERED` -> delivered). `RTO
DELIVERED` is deliberately NOT matched by `DELIVERED` (exact match).
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.courier import Courier
from app.models.shipment import ShipmentEvent

PICKUP_EVENT_LABELS = ("PICKED UP", "SHIPPED")
DELIVERED_EVENT_LABELS = ("DELIVERED",)


def _first_event_time(shipment, labels: tuple[str, ...]):  # noqa: ANN001
    return (
        select(func.min(ShipmentEvent.event_timestamp))
        .where(
            ShipmentEvent.shipment_id == shipment.id,
            func.upper(func.trim(ShipmentEvent.status)).in_(labels),
        )
        .correlate(shipment)
        .scalar_subquery()
    )


def effective_pickup_date(shipment):  # noqa: ANN001, ANN201
    return func.coalesce(shipment.pickup_date, _first_event_time(shipment, PICKUP_EVENT_LABELS))


def effective_delivery_date(shipment):  # noqa: ANN001, ANN201
    return func.coalesce(
        shipment.actual_delivery_date, _first_event_time(shipment, DELIVERED_EVENT_LABELS)
    )


def effective_courier_name(shipment):  # noqa: ANN001, ANN201
    """The linked `Courier.name`, else the most recent non-empty courier
    name any tracking event for this shipment carried.
    """
    linked = (
        select(Courier.name)
        .where(Courier.id == shipment.courier_id)
        .correlate(shipment)
        .scalar_subquery()
    )
    from_events = (
        select(ShipmentEvent.courier_name)
        .where(
            ShipmentEvent.shipment_id == shipment.id,
            ShipmentEvent.courier_name.is_not(None),
            func.trim(ShipmentEvent.courier_name) != "",
        )
        .order_by(ShipmentEvent.event_timestamp.desc())
        .limit(1)
        .correlate(shipment)
        .scalar_subquery()
    )
    return func.coalesce(linked, from_events)


def partner_key(name: str | None) -> str | None:
    """Case/whitespace-insensitive identity for a courier name, so
    "Delhivery", "delhivery " and a linked `Courier("Delhivery")` are one
    partner.
    """
    if name is None:
        return None
    key = " ".join(name.split()).lower()
    return key or None


def partner_identity(name: str | None, courier_id) -> str | None:  # noqa: ANN001
    """Stable grouping key for a shipping partner: its (normalised) name
    when any is known, else the linked courier id, else `None` (no
    partner at all -- the order is not allocated to anyone).
    """
    key = partner_key(name)
    if key is not None:
        return key
    return f"id:{courier_id}" if courier_id is not None else None
