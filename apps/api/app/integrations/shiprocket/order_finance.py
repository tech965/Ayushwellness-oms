"""Finance fields of Shiprocket's documented `GET /orders/show/{order_id}`.

The public API collection shows `data.awb_data.charges` (freight, COD fee,
weights, weight-based and RTO amounts, zone) and `data.remittance_utr`,
`data.remittance_date`, `data.remittance_status`. It documents NO remitted
amount, deductions or payout (CRF) id, so none is derived here. Blank or
unreadable values stay `None`; nothing becomes 0.

The order response also carries customer details, which are never kept.
"""

from __future__ import annotations

from typing import Any

from app.integrations.shiprocket.remittance import _dt, _money

CHARGE_AMOUNTS = (
    "freight_charges",
    "cod_charges",
    "applied_weight_amount",
    "charged_weight_amount",
    "charged_weight_amount_rto",
    "applied_weight_amount_rto",
)
CHARGE_WEIGHTS = ("applied_weight", "charged_weight")


def _text(value: Any, limit: int) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text[:limit] if text and text.upper() not in ("NA", "N/A") else None


def parse_order_finance(body: Any) -> dict[str, Any] | None:
    """None when the response is not an order-detail body at all."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    awb_data = data.get("awb_data") if isinstance(data.get("awb_data"), dict) else {}
    charges = awb_data.get("charges") if isinstance(awb_data.get("charges"), dict) else {}
    amounts = {
        key: _money(charges.get(key)) if charges.get(key) not in (None, "") else None
        for key in (*CHARGE_AMOUNTS, *CHARGE_WEIGHTS)
    }
    return {
        "charges": amounts,
        "zone": _text(charges.get("zone"), 32),
        "has_charges": any(amounts[key] is not None for key in CHARGE_AMOUNTS),
        "remittance_utr": _text(data.get("remittance_utr"), 128),
        "remittance_status": _text(data.get("remittance_status"), 64),
        "remittance_date": _dt(_text(data.get("remittance_date"), 40)),
        "raw": {
            "awb_data_charges": {k: charges.get(k) for k in charges},
            "remittance_utr": data.get("remittance_utr"),
            "remittance_status": data.get("remittance_status"),
            "remittance_date": data.get("remittance_date"),
        },
    }
