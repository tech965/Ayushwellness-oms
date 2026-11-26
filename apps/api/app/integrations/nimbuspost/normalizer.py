"""NimbusPost webhook payload parsing -- New (v2) B2C format only.

Every field name and status code below is taken directly from
NimbusPost's official webhook reference (2026-11-26), never guessed:
the flat 23-key B2C body (every value a string, `""` when the event
doesn't carry it -- never missing or null), and the fixed `status`/
`status_code` table for `tracking.updated`. B2B cargo payloads (a
structurally different body: `cargo_order_id`, `child_awbs`, a nested
`scan` object) are explicitly out of scope -- see
`app.api.v1.webhooks.couriers.receive_nimbuspost_webhook`'s docstring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.enums import ShipmentStatus

# --- tracking.updated: the one vocabulary with a fixed status_code -----
#
# Confirmed table ("Status codes", New format). `damaged`/`lost` (DG/LT)
# are deliberately NOT mapped to any `ShipmentStatus` -- per instruction,
# they must be recorded/logged only, never drive a state transition
# (there is no "damaged"/"lost" concept in `ShipmentStatus` today, and
# folding them into NDR/CANCELLED would misrepresent what happened).
_TRACKING_STATUS_TO_SHIPMENT_STATUS: dict[str, ShipmentStatus] = {
    "PP": ShipmentStatus.PENDING,  # pending pickup
    "PKD": ShipmentStatus.PICKED_UP,  # picked
    "PF": ShipmentStatus.PENDING,  # pickup failed -- still pending pickup
    "PEX": ShipmentStatus.PENDING,  # pickup exception -- still pending pickup
    "IT": ShipmentStatus.IN_TRANSIT,  # in transit
    "RAD": ShipmentStatus.IN_TRANSIT,  # reached destination hub -- still in transit
    "OFD": ShipmentStatus.OUT_FOR_DELIVERY,  # out for delivery
    "DL": ShipmentStatus.DELIVERED,  # delivered
    "EX": ShipmentStatus.NDR,  # exception (failed delivery attempt / NDR)
    "RT": ShipmentStatus.RTO_INITIATED,  # rto initiated
    "RT-IT": ShipmentStatus.RTO_INITIATED,  # rto in transit
    "RT-OFD": ShipmentStatus.RTO_INITIATED,  # rto out for delivery
    "RT-UD": ShipmentStatus.RTO_INITIATED,  # rto ud (return delivery attempt failed)
    "RT-DL": ShipmentStatus.RTO_DELIVERED,  # rto delivered
    "CAN": ShipmentStatus.CANCELLED,  # cancelled
    # "DG" (damaged) and "LT" (lost) intentionally absent -- see above.
}

# Explicitly unsupported per business decision: recorded/logged, never
# mapped, never a trigger for any inventory or shipment-state change.
UNSUPPORTED_STATUS_CODES = frozenset({"DG", "LT"})


def normalize_tracking_status_code(status_code: str) -> ShipmentStatus | None:
    """`None` for an unmapped code -- including `DG`/`LT` (unsupported,
    logged separately by the caller) and any future/unknown code
    (`UNK` or otherwise) NimbusPost's own docs say to "log, but not act
    on." The caller must never guess a status for a `None` result.
    """
    return _TRACKING_STATUS_TO_SHIPMENT_STATUS.get(status_code)


# --- order.updated: a DIFFERENT vocabulary, deliberately narrow use ----
#
# Per business decision, `order.updated` is used ONLY for AWB/identifier
# backfill (status "booked" or "" -- "the shipment got a new AWB") and
# cancellation ("cancelled") -- `tracking.updated` is authoritative for
# every other real status transition, so nothing else here is mapped to
# avoid the same transition being decided twice by two different event
# types. See the module docstring on why this split exists.
_ORDER_UPDATED_AWB_ONLY_STATUSES = frozenset({"booked", ""})
_ORDER_UPDATED_CANCELLED_STATUS = "cancelled"


def order_updated_kind(status: str) -> str:
    """Returns `"awb_update"`, `"cancelled"`, or `"not_handled"` (every
    other `order.updated` status -- shipped/in transit/delivered/rto */
    etc. -- intentionally left to `tracking.updated`)."""
    normalized = (status or "").strip().lower()
    if normalized in _ORDER_UPDATED_AWB_ONLY_STATUSES:
        return "awb_update"
    if normalized == _ORDER_UPDATED_CANCELLED_STATUS:
        return "cancelled"
    return "not_handled"


def _parse_utc_datetime(value: str) -> datetime | None:
    """NimbusPost's New-format B2C `event_time`/`edd`/dates are
    documented as UTC, `YYYY-MM-DD HH:mm:ss` (or `YYYY-MM-DD` for a
    date-only field) -- one confirmed format, no defensive multi-format
    guessing needed (unlike Shiprocket's own unconfirmed shape)."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class NimbusPostTrackingWebhookNormalizer:
    """One `tracking.updated` New-format B2C body -> the kwargs shape
    `app.integrations.shiprocket.sync.apply_tracking_event` expects
    (confirmed against `ShiprocketWebhookTrackingNormalizer`'s own
    output shape -- the two must match exactly since both feed the same
    function).
    """

    def normalize_event(self, raw: dict[str, Any]) -> dict[str, Any]:
        status_code = (raw.get("status_code") or "").strip()
        return {
            # No stable per-SCAN id in the documented body (only the
            # delivery-level `x-nimbus-delivery`, already the
            # `WebhookEvent.external_event_id` -- see the webhook
            # route). `ShipmentEventRepository.find_duplicate` already
            # falls back to a (status, event_timestamp) check when this
            # is `None`, same as Shiprocket's own webhook normalizer.
            "external_event_id": None,
            "status": raw.get("status") or status_code or "",
            "mapped_status": normalize_tracking_status_code(status_code),
            "location": raw.get("location") or None,
            "event_timestamp": _parse_utc_datetime(raw.get("event_time") or ""),
            "description": raw.get("message") or raw.get("status") or None,
            "courier_name": raw.get("courier_name") or None,
            "expected_delivery_date": _parse_utc_datetime(raw.get("edd") or ""),
            "raw_payload": raw,
        }


NIMBUSPOST_TRACKING_WEBHOOK_NORMALIZER = NimbusPostTrackingWebhookNormalizer()


def extract_webhook_identifiers(payload: dict[str, Any]) -> dict[str, str | None]:
    """AWB / NimbusPost's own order id / your order number -- the three
    identifiers the webhook service tries, in that preferred order, to
    find the OMS `Shipment` a delivery refers to. Confirmed field names
    only (`awb_number`, `order_id`, `order_number`) -- never a guessed
    alias, unlike Shiprocket's defensive multi-key fallback (its shape
    was never confirmed; NimbusPost's is).
    """

    def _clean(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    return {
        "awb": _clean(payload.get("awb_number")),
        "nimbuspost_order_id": _clean(payload.get("order_id")),
        "order_number": _clean(payload.get("order_number")),
    }
