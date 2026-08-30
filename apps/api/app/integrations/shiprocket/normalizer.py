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

import re
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


# Strips an ordinal day suffix ("18th" -> "18", "21st" -> "21") --
# confirmed live: GET /shipments' `created_at` uses one ("18th Dec 2025
# 03:52 PM"), but GET /orders/show/{id}'s `created_at` does not ("21 Dec
# 2025 12:49 PM") -- same provider, two endpoints, two subtly different
# formats. `strptime` has no directive for an ordinal suffix, so it's
# stripped before parsing rather than adding a third, near-duplicate
# format string.
_ORDINAL_SUFFIX_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.IGNORECASE)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = _ORDINAL_SUFFIX_RE.sub(r"\1", value)
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        # Confirmed live on GET /orders/show/{id}'s `created_at` (e.g.
        # "21 Dec 2025 12:49 PM") and, once the ordinal suffix above is
        # stripped, GET /shipments' `created_at` too (e.g. "18th Dec 2025
        # 03:52 PM" -> "18 Dec 2025 03:52 PM").
        "%d %b %Y %I:%M %p",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
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


# --- Tracking webhook (push, inbound) ----------------------------------

# UNVERIFIED — no live Shiprocket webhook delivery has been captured
# (see docs/integrations/shiprocket.md's Webhooks section for what was
# and wasn't confirmed). These are the field names third-party
# integration guides most consistently describe for Shiprocket's
# "Shipment Webhook" (Settings > API > Webhook) payload; every key is
# read defensively with a fallback chain, exactly like the rest of this
# module, so a wrong guess degrades to "field not found" (None) rather
# than a crash or a fabricated value. Re-confirm this list against a
# real delivery (see the endpoint's logging) before treating any single
# alias as authoritative.
def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def extract_webhook_shipment_identifiers(payload: dict[str, Any]) -> dict[str, str | None]:
    """AWB / Shiprocket shipment id / Shiprocket order id / channel
    (Shopify) order number — the four identifiers
    `ShiprocketWebhookService` tries, in that preferred order, to find
    the OMS `Shipment` a webhook delivery refers to. Never reads
    anything that isn't one of those four (spec: never guess from
    name/phone/address).
    """
    awb = _first_present(payload, "awb", "awb_code")
    shipment_id = _first_present(payload, "shipment_id", "sr_shipment_id")
    order_id = _first_present(payload, "order_id", "sr_order_id")
    channel_order_id = _first_present(
        payload, "channel_order_id", "channel_order_number", "reference_number"
    )
    courier_name = _first_present(payload, "courier_name", "courier")
    return {
        "awb": str(awb) if awb is not None else None,
        "shiprocket_shipment_id": (str(shipment_id) if shipment_id is not None else None),
        "shiprocket_order_id": (str(order_id) if order_id is not None else None),
        "channel_order_id": (str(channel_order_id) if channel_order_id is not None else None),
        "courier_name": str(courier_name) if courier_name is not None else None,
    }


class ShiprocketWebhookTrackingNormalizer:
    """One flat webhook body (no `scans`/`shipment_track_activities`
    list — see `extract_tracking_events`, tried first by the caller for
    a payload that does have one, in which case `TRACKING_NORMALIZER`
    handles it instead) -> the same kwargs shape
    `ShipmentService.add_tracking_event` expects.

    Deliberately never reads a body-level `id`/`shipment_id` as
    `external_event_id` — unlike a `shipment_track_activities` list item
    (where `id` genuinely identifies one scan), a webhook body's `id`
    identifies the *shipment*, not this one status-change event; treating
    it as a per-event id would make a later, genuinely different status
    update for the same shipment collide with an earlier one and silently
    disappear. `external_event_id` is always `None` here instead, which
    is exactly the case `ShipmentEventRepository.find_duplicate` already
    handles by falling back to a `(status, event_timestamp)` check (see
    `app.models.shipment`'s module docstring) — a genuine retry of the
    same event is still deduplicated; a real, later status change is not.
    """

    def normalize_event(self, raw: dict[str, Any]) -> dict[str, Any]:
        raw_status = _first_present(raw, "current_status", "status", "shipment_status")
        return {
            "external_event_id": None,
            "status": raw_status or "",
            "mapped_status": normalize_shipment_status(raw_status),
            "location": _first_present(raw, "current_location", "location", "city"),
            "event_timestamp": _parse_datetime(
                _first_present(raw, "current_timestamp", "updated_at", "status_updated_at", "date")
            ),
            "description": _first_present(raw, "current_status_label", "activity") or raw_status,
            "courier_name": _first_present(raw, "courier_name", "courier"),
            "raw_payload": raw,
        }


WEBHOOK_TRACKING_NORMALIZER = ShiprocketWebhookTrackingNormalizer()


# --- Shipments (pull) --------------------------------------------------


class ShiprocketShipmentNormalizer:
    """One raw item from `GET /shipments` -> the kwargs
    `app.integrations.entity_sync._upsert_shipment` expects — a superset
    of what `ShipmentService.upsert_synced_shipment` takes, plus
    `channel_order_id`/`shiprocket_order_id`/`shiprocket_created_at` (all
    three popped by the handler, never passed through to the service),
    since matching to an OMS `Order` is provider-specific logic that
    doesn't belong inside a generic shipment upsert. `shiprocket_created_at`
    exists purely so the handler can skip an expensive live order-detail
    lookup for a shipment old enough to predate the OMS's own order-sync
    coverage entirely — a performance boundary, not a matching decision.

    Field names, confirmed against a real production `/shipments`
    response: `id` is the Shiprocket shipment id (`external_id` below —
    this is what `entity_sync._upsert_shipment` uses to find an
    already-known `Shipment` first, before ever attempting an order
    lookup). `awb`/`status` are confirmed present too, though `awb` is
    empty (`""`) until a shipment leaves `PENDING`.

    `channel_order_id` does **not** exist on this endpoint at all —
    unlike the NDR listing, which really does have it (see
    `ShiprocketNDRNormalizer`). This always reads as `None` here; that's
    expected, not a bug. `order_id` (Shiprocket's own internal numeric
    order id, distinct from `id`/`external_id` above) *is* present here
    though, and is passed through as `shiprocket_order_id` purely so
    `entity_sync._upsert_shipment` can fall back to `GET
    /orders/show/{order_id}` — the one endpoint confirmed live to return
    `channel_order_id` reliably — when a shipment has no existing
    `Shipment` row and no usable `channel_order_id` from this endpoint.
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
            "shiprocket_order_id": (
                str(raw["order_id"]) if raw.get("order_id") is not None else None
            ),
            "shiprocket_created_at": _parse_datetime(raw.get("created_at")),
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
