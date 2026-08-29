"""Shiprocket <-> OMS mapping — both directions:

- **Pull** (Shiprocket -> OMS): tracking events/status, NDR records.
- **Push** (OMS -> Shiprocket): the order-create payload built from an
  OMS `Order`.

Every raw field is read defensively (`.get()`, safe fallbacks) since
Shiprocket's exact response shape could not be verified against a live
account — see `docs/integrations/shiprocket.md` for what needs
re-confirmation before production use. An unrecognized status never
raises; it's preserved as free text on `ShipmentEvent.status` even when
it doesn't map to a known `ShipmentStatus` enum value (spec §14).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.enums import PaymentType, ShipmentStatus
from app.models.mixins import SourceSystem

# --- Shiprocket -> OMS shipment status -------------------------------

# Verify against a live account: these are the commonly documented
# Shiprocket tracking status strings, but Shiprocket does not publish a
# single authoritative enum list. Matching is case/whitespace-insensitive
# (see `_normalize_key`). An unmapped status returns None — the caller
# must never guess a status; it just doesn't update `Shipment.current_status`.
_SHIPMENT_STATUS_MAP: dict[str, ShipmentStatus] = {
    "NEW": ShipmentStatus.PENDING,
    "PICKUP SCHEDULED": ShipmentStatus.PENDING,
    "PICKUP GENERATED": ShipmentStatus.PENDING,
    "PICKED UP": ShipmentStatus.PICKED_UP,
    "SHIPPED": ShipmentStatus.PICKED_UP,
    "IN TRANSIT": ShipmentStatus.IN_TRANSIT,
    "OUT FOR DELIVERY": ShipmentStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ShipmentStatus.DELIVERED,
    "UNDELIVERED": ShipmentStatus.NDR,
    "NDR": ShipmentStatus.NDR,
    "CANCELLED": ShipmentStatus.CANCELLED,
    "CANCELED": ShipmentStatus.CANCELLED,
    "RTO INITIATED": ShipmentStatus.RTO_INITIATED,
    "RTO IN TRANSIT": ShipmentStatus.RTO_INITIATED,
    "RTO": ShipmentStatus.RTO_INITIATED,
    "RTO DELIVERED": ShipmentStatus.RTO_DELIVERED,
}


def _normalize_key(raw: str | None) -> str:
    return (raw or "").strip().upper()


def normalize_shipment_status(raw_status: str | None) -> ShipmentStatus | None:
    return _SHIPMENT_STATUS_MAP.get(_normalize_key(raw_status))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- Tracking (pull) ------------------------------------------------------


def extract_tracking_events(raw_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Shiprocket's tracking response shape is not fully documented
    without a live account — this reads the commonly-cited
    `tracking_data.shipment_track_activities` shape, with fallbacks, and
    returns an empty list (not an error) if nothing recognizable is
    found, so an unexpected shape degrades to "no new events" rather
    than crashing the caller.
    """
    tracking_data = raw_response.get("tracking_data") or raw_response
    events = (
        tracking_data.get("shipment_track_activities") or tracking_data.get("shipment_track") or []
    )
    return events if isinstance(events, list) else []


class ShiprocketTrackingNormalizer:
    """One raw tracking-activity dict (from `GET .../track/awb/{awb}`,
    `shipment_track_activities`-style shape) -> the kwargs
    `ShipmentService.add_tracking_event` expects.
    """

    def normalize_event(self, raw: dict[str, Any]) -> dict[str, Any]:
        raw_status = raw.get("status") or raw.get("current_status") or raw.get("activity") or ""
        return {
            "external_event_id": (str(raw["id"]) if raw.get("id") is not None else None),
            "status": raw_status,
            "mapped_status": normalize_shipment_status(raw_status),
            "location": raw.get("location"),
            "event_timestamp": _parse_datetime(raw.get("date") or raw.get("timestamp")),
            "description": raw.get("activity") or raw.get("sr-status-label") or raw_status or None,
            "courier_name": raw.get("courier_name"),
            "raw_payload": raw,
        }


TRACKING_NORMALIZER = ShiprocketTrackingNormalizer()


# --- Shipments (pull) --------------------------------------------------


class ShiprocketShipmentNormalizer:
    """One raw item from `GET /shipments` -> the kwargs
    `app.integrations.entity_sync._upsert_shipment` expects — a superset
    of what `ShipmentService.upsert_synced_shipment` takes, plus
    `channel_order_id` (popped by the handler, never passed through to
    the service), since matching to an OMS `Order` is provider-specific
    logic that doesn't belong inside a generic shipment upsert.

    Field names, confirmed against a real production `/shipments`
    response: `id` is the Shiprocket shipment id (`external_id` below —
    this is what `entity_sync._upsert_shipment` uses to find an
    already-known `Shipment` first, before ever attempting an order
    lookup). `awb`/`status` are confirmed present too, though `awb` is
    empty (`""`) until a shipment leaves `PENDING`.

    `channel_order_id` does **not** exist on this endpoint at all —
    unlike the NDR listing, which really does have it (see
    `ShiprocketNDRNormalizer`). This always reads as `None` here; that's
    expected, not a bug, and is exactly why `_upsert_shipment` no longer
    depends on it for a shipment this OMS already has a `Shipment` row
    for. No reliable replacement field for a genuinely *new* shipment
    (one this OMS has never created) has been confirmed yet — real data
    for a `PENDING` shipment shows `number`/`code` both empty too.
    """

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        external_id = str(raw.get("id") or raw.get("shipment_id") or "")
        raw_status = raw.get("status") or raw.get("status_code") or raw.get("current_status")
        return {
            "source_system": SourceSystem.SHIPROCKET,
            "external_id": external_id,
            "shiprocket_shipment_id": external_id or None,
            "channel_order_id": (
                str(raw["channel_order_id"]) if raw.get("channel_order_id") is not None else None
            ),
            "awb": raw.get("awb") or raw.get("awb_code"),
            "current_status": normalize_shipment_status(raw_status),
            "raw_external_payload": raw,
        }


SHIPMENT_NORMALIZER = ShiprocketShipmentNormalizer()


# --- NDR (pull) -------------------------------------------------------


class ShiprocketNDRNormalizer:
    """One raw NDR-list-item dict -> the kwargs `NDRService.upsert_synced_ndr`
    expects.

    Field names confirmed against a LIVE `GET /ndr/all` response (see the
    normalizer test file for the exact captured shape): `awb_code` (not
    `awb`) and `courier` (not `courier_name`) — the previous guesses,
    documented as unverified, were wrong for these two, which is a load-
    bearing bug: `NDRService.upsert_synced_ndr` resolves the owning
    `Shipment` via `awb` and raises `NotFoundError` when it's `None`
    (spec §16, "Do not invent NDR data" — it refuses to guess a
    shipment instead of silently dropping the AWB check), so every real
    NDR record failed to sync at all, not just displayed blank. `reason`,
    `attempts`, and `id` already matched the live shape and are
    unchanged. The old guessed names are kept as a second fallback in
    case another Shiprocket response variant (a different endpoint/API
    version) still uses them.
    """

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        external_id = str(raw.get("id") or raw.get("ndr_id") or raw.get("awb_code") or "")
        reason = raw.get("reason") or raw.get("nsl_code")
        return {
            "source_system": SourceSystem.SHIPROCKET,
            "external_id": external_id,
            "awb": raw.get("awb_code") or raw.get("awb"),
            "shiprocket_order_id": (
                str(raw["order_id"]) if raw.get("order_id") is not None else None
            ),
            "reason": reason,
            "external_reason": reason,
            "attempt_number": int(raw.get("attempts") or raw.get("attempt") or 1),
            "courier_name": raw.get("courier") or raw.get("courier_name"),
            "external_created_at": _parse_datetime(
                raw.get("ndr_raised_at") or raw.get("created_at") or raw.get("ndr_raised_date")
            ),
            "external_updated_at": _parse_datetime(raw.get("updated_at")),
            "raw_external_payload": raw,
        }


NDR_NORMALIZER = ShiprocketNDRNormalizer()


# --- Order creation (push) -------------------------------------------------

# OMS PaymentType -> Shiprocket's accepted `payment_method` values.
# Verify against a live account before first real use.
_PAYMENT_METHOD_MAP: dict[PaymentType, str] = {
    PaymentType.COD: "COD",
    PaymentType.PREPAID: "Prepaid",
    PaymentType.OTHER: "Prepaid",
}


def normalize_payment_method(payment_type: PaymentType) -> str:
    return _PAYMENT_METHOD_MAP.get(payment_type, "Prepaid")


class ShiprocketOrderPushNormalizer:
    """Builds the `POST /orders/create/adhoc` request body from an OMS
    `Order` (with `.items`, `.customer`, `.shipping_address` eager-loaded)
    plus the configured pickup location. Field names match the commonly
    documented adhoc-order shape — re-verify against a live account.
    """

    def build_payload(
        self,
        order: Any,
        *,
        pickup_location: str,
        length_cm: float = 10.0,
        breadth_cm: float = 10.0,
        height_cm: float = 10.0,
        weight_kg: float = 0.5,
    ) -> dict[str, Any]:
        """`length_cm`/`breadth_cm`/`height_cm`/`weight_kg` default to a
        placeholder small-parcel package — the OMS has no per-order
        package-dimension data today (see docs/roadmap.md's Phase 2.3
        known-limitations entry). The operator can override these when
        triggering shipment creation; a future phase could derive them
        from `ProductVariant.weight` instead.
        """
        address = order.shipping_address or {}
        customer = order.customer
        first_name, _, last_name = (address.get("contact_name") or "").partition(" ")
        if not first_name:
            first_name = (customer.first_name if customer else None) or "Customer"
        if not last_name:
            last_name = (customer.last_name if customer else None) or ""

        return {
            "order_id": str(order.order_number),
            "order_date": order.order_datetime.strftime("%Y-%m-%d %H:%M"),
            "pickup_location": pickup_location,
            "billing_customer_name": first_name,
            "billing_last_name": last_name,
            "billing_address": address.get("line1") or "",
            "billing_address_2": address.get("line2") or "",
            "billing_city": address.get("city") or "",
            "billing_pincode": address.get("pin_code") or "",
            "billing_state": address.get("state") or "",
            "billing_country": address.get("country") or "India",
            "billing_email": (customer.email if customer else None) or "",
            "billing_phone": address.get("contact_phone")
            or (customer.phone if customer else None)
            or "",
            "shipping_is_billing": True,
            "order_items": [
                {
                    "name": item.product_name,
                    "sku": item.sku,
                    "units": item.quantity,
                    "selling_price": str(item.unit_price),
                }
                for item in order.items
            ],
            "payment_method": normalize_payment_method(order.payment_type),
            "sub_total": str(order.subtotal),
            "length": length_cm,
            "breadth": breadth_cm,
            "height": height_cm,
            "weight": weight_kg,
        }


ORDER_PUSH_NORMALIZER = ShiprocketOrderPushNormalizer()
