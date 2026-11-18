"""Normalizes Shiprocket COD remittance records.

The exact response shape of Shiprocket's remittance feed is not verified
against a live account, so this reads each field from a list of plausible
key names and returns `None` for anything absent -- it never invents or
derives a value (e.g. it never turns "delivered" into "remitted").
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_AWB = ("awb_code", "awb", "awb_number", "tracking_number")
_SR_ORDER = ("order_id", "sr_order_id", "shiprocket_order_id")
_CHANNEL_ORDER = ("channel_order_id", "order_number", "channel_order_no", "reference_order_id")
_COD_AMOUNT = ("cod_amount", "cod_payable", "order_cod_amount", "cod_charges_amount")
_COLLECTED = ("collected_amount", "cod_collected", "amount_collected", "cod_amount_collected")
_COLLECTED_AT = ("collected_at", "delivered_date", "delivered_at", "delivery_date")
_REMITTED = ("remitted_amount", "remittance_amount", "amount_remitted", "remitted_cod_amount")
_STATUS = ("remittance_status", "status", "payment_status")
_REFERENCE = ("utr", "utr_no", "remittance_utr", "crf_id", "remittance_id", "reference")
_SETTLED = ("remittance_date", "remitted_at", "settlement_date", "paid_date", "payment_date")


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", "NA", "N/A"):
            return value
    return None


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%d-%m-%Y", "%d %b %Y, %I:%M %p"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalize_cod_remittance(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the `CodRemittance` columns for one record, or `None` when
    the record carries no AWB (nothing to attach it to)."""
    awb = _first(raw, _AWB)
    if awb is None:
        return None
    sr_order = _first(raw, _SR_ORDER)
    channel = _first(raw, _CHANNEL_ORDER)
    status = _first(raw, _STATUS)
    reference = _first(raw, _REFERENCE)
    return {
        "awb": str(awb).strip(),
        "shiprocket_order_id": str(sr_order) if sr_order is not None else None,
        "channel_order_id": str(channel) if channel is not None else None,
        "cod_amount": _money(_first(raw, _COD_AMOUNT)),
        "collected_amount": _money(_first(raw, _COLLECTED)),
        "collected_at": _dt(_first(raw, _COLLECTED_AT)),
        "remitted_amount": _money(_first(raw, _REMITTED)),
        "remittance_status": str(status)[:64] if status is not None else None,
        "remittance_reference": str(reference)[:128] if reference is not None else None,
        "settlement_date": _dt(_first(raw, _SETTLED)),
    }
