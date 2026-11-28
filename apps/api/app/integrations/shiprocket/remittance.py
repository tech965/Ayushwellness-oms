"""Normalizes Shiprocket COD remittance records.

CONFIRMED DIRECTLY BY SHIPROCKET SUPPORT (current, correct, in effect):
`POST https://api.shiprocket.in/v1/cod/remittance` (see
`ShiprocketAdapter.fetch_cod_remittances`) returns remittance entries with
EXACTLY three fields: `awb`, `amount`, `status` (one of `pending` /
`processed` / `failed`). This endpoint does NOT return a CRF id, UTR,
transaction date, or order id at all -- `normalize_cod_remittance()` below
correctly returns `None` for all of those (via `_first()`'s alias lookup
finding nothing), which is intentional: this module must never invent a
value for a field the API doesn't actually return.

Two earlier, now-SUPERSEDED attempts both turned out to be wrong:
1. `GET https://apiv2.shiprocket.in/v1/account/details/remittance` --
   500'd on every real sync call (wrong endpoint entirely).
2. `GET https://apiv2.shiprocket.in/v1/cod/remittance` (right path,
   wrong base URL -- joining it onto the shared `/v1/external` base
   produced `.../v1/external/cod/remittance`, which 404'd) with a
   `pending`/`initiated`/`success` status vocabulary and an assumed
   `crf_id`/`utr`/`date`/`order_id`-bearing response shape that was never
   actually confirmed against a real response.
The alias tuples below still list those superseded field names (`crf_id`,
`utr`, `date`, `order_id`) as secondary aliases, in case Shiprocket ever
adds them to a future response -- but only `awb`, `amount` (via the
`amount` alias), and `status` are expected to ever actually match today.

`crf_id` (an entry's own identifier, when present) and `utr` (the bank
transaction reference) are DIFFERENT fields and must not share one alias
list: a record can carry both, and the CRF id is what makes repeat syncs
of the same payout idempotent (see `_CRF_ID` / dedupe_key usage in
`CodReconciliationService`) when it is present -- which, for the current
confirmed endpoint, it never is; `CodReconciliationService` falls back to
a deterministic (awb, amount, status) fingerprint in that case (see its
own docstring for the honest limitation this implies).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_AWB = ("awb", "awb_code", "awb_number", "tracking_number")
_SR_ORDER = ("order_id", "sr_order_id", "shiprocket_order_id")
_CHANNEL_ORDER = ("channel_order_id", "order_number", "channel_order_no", "reference_order_id")
_COD_AMOUNT = ("cod_amount", "cod_payable", "order_cod_amount", "cod_charges_amount")
_COLLECTED = ("collected_amount", "cod_collected", "amount_collected", "cod_amount_collected")
_COLLECTED_AT = ("collected_at", "delivered_date", "delivered_at", "delivery_date")
_REMITTED = (
    "amount",
    "remitted_amount",
    "remittance_amount",
    "amount_remitted",
    "remitted_cod_amount",
)
_STATUS = ("status", "remittance_status", "payment_status")
_REFERENCE = ("utr", "utr_no", "remittance_utr", "remittance_id", "reference")
_CRF_ID = ("crf_id",)
_SETTLED = (
    "date",
    "remittance_date",
    "remitted_at",
    "settlement_date",
    "paid_date",
    "payment_date",
)

# The CRF endpoint's own, confirmed-real status vocabulary. Nothing else is
# ever treated as one of these three -- an unrecognised status string is
# kept verbatim in `remittance_status` but never matched here. `failed`
# must never be read as `pending` or `processed` anywhere downstream
# (see `CodReconciliationService.sync_remittances` and
# `CodRemittanceReconciliationService`'s `payment_failed` KPI).
CRF_STATUSES = frozenset({"pending", "processed", "failed"})


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
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y",
        "%d-%m-%Y",
        "%d %b %Y, %I:%M %p",
        # Other common day-first / month-name renderings. Parsing only: a date that
        # still fails to parse is dropped, so these keep a reported date from being lost.
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%b %d, %Y",
        "%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M",
    ):
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
    crf_id = _first(raw, _CRF_ID)
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
        "crf_id": str(crf_id) if crf_id is not None else None,
    }
