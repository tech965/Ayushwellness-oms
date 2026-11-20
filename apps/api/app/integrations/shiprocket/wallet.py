"""Parsers for Shiprocket wallet data.

The response shapes of the wallet-balance, statement and serviceability
endpoints are NOT verified against a live account (Shiprocket's reference
docs are not machine-readable here). So every parser reads each field from
a list of plausible keys and returns `None` when nothing matches -- it never
invents a value, never turns a missing balance into 0, and never guesses a
transaction direction.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.integrations.shiprocket.remittance import _dt, _first

_BALANCE_KEYS = ("balance_amount", "balance", "wallet_balance", "available_balance")


def _num(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError):
        return None


def parse_wallet_balance(body: Any, *, _depth: int = 0) -> Decimal | None:
    """Finds the wallet balance in a response of unverified shape."""
    if not isinstance(body, dict) or _depth > 3:
        return None
    for key in _BALANCE_KEYS:
        if key in body:
            value = _num(body[key])
            if value is not None:
                return value
    for value in body.values():
        if isinstance(value, dict):
            found = parse_wallet_balance(value, _depth=_depth + 1)
            if found is not None:
                return found
    return None


def extract_rate_estimate(
    body: Any, *, courier_id: str | None = None
) -> tuple[Decimal, str | None] | None:
    """(estimated charge, courier name) from a serviceability response.

    With a specific courier, that courier's rate. Without one (Shiprocket
    picks), the recommended courier if the response names one, otherwise the
    HIGHEST available rate so the balance check errs on the safe side.
    Returns None when no numeric rate can be read.
    """
    if not isinstance(body, dict):
        return None
    data = body.get("data", body)
    companies = data.get("available_courier_companies") if isinstance(data, dict) else None
    if not isinstance(companies, list):
        return None

    def rate_of(c: dict[str, Any]) -> Decimal | None:
        direct = _num(c.get("rate"))
        if direct is not None:
            return direct
        parts = [_num(c.get(k)) for k in ("freight_charge", "cod_charges")]
        parts = [p for p in parts if p is not None]
        return sum(parts, Decimal("0")) if parts else None

    rated = [(c, rate_of(c)) for c in companies if isinstance(c, dict)]
    rated = [(c, r) for c, r in rated if r is not None]
    if not rated:
        return None
    chosen: tuple[dict[str, Any], Decimal] | None = None
    if courier_id:
        chosen = next(
            ((c, r) for c, r in rated if str(c.get("courier_company_id")) == str(courier_id)),
            None,
        )
    if chosen is None and not courier_id:
        rec = data.get("recommended_courier_company_id") if isinstance(data, dict) else None
        if rec is not None:
            chosen = next(
                ((c, r) for c, r in rated if str(c.get("courier_company_id")) == str(rec)), None
            )
    if chosen is None:
        if courier_id:
            return None  # asked for a specific courier we cannot price
        chosen = max(rated, key=lambda cr: cr[1])
    return chosen[1], chosen[0].get("courier_name")


# ----------------------------------------------------------------- statement

_REF = ("transaction_id", "txn_id", "reference_no", "reference_number", "id")
_DATE = ("transaction_date", "txn_date", "date", "created_at", "created_date")
_AMOUNT = ("amount", "transaction_amount")
_DIR = ("type", "transaction_type", "credit_debit", "cr_dr", "entry_type")
_ORDER = ("channel_order_id", "order_number", "order_id")
_AWB = ("awb_code", "awb", "awb_number")
_COURIER = ("courier_name", "courier", "courier_company")
_DESC = ("description", "remarks", "narration", "note", "details")
_STATUS = ("status", "transaction_status")
_PAYREF = ("payment_reference", "payment_id", "utr", "gateway_reference")
_BAL = ("balance", "closing_balance", "running_balance", "balance_after")


def _direction(raw: dict[str, Any]) -> str | None:
    text = str(_first(raw, _DIR) or "").strip().lower()
    if text in ("cr", "credit", "credited") or "credit" in text:
        return "credit"
    if text in ("dr", "debit", "debited") or "debit" in text:
        return "debit"
    credit, debit = _num(raw.get("credit")), _num(raw.get("debit"))
    if credit and not debit:
        return "credit"
    if debit and not credit:
        return "debit"
    return None


def classify(direction: str, text: str) -> str:
    t = text.lower()
    if direction == "credit":
        if "recharge" in t or "wallet add" in t or "added" in t:
            return "recharge"
        if "cod" in t and ("revers" in t or "refund" in t):
            return "cod_reversal"
        if "revers" in t or "freight" in t:
            return "freight_reversal"
        if "refund" in t:
            return "refund"
        return "other_credit"
    if "weight" in t and any(k in t for k in ("discrepan", "dispute", "excess", "difference")):
        return "weight_discrepancy"
    if "rto" in t:
        return "rto_freight"
    if "cod" in t:
        return "cod_charge"
    if "freight" in t or "shipping" in t or "forward" in t:
        return "freight"
    return "other_debit"


def _status(raw: dict[str, Any]) -> str:
    text = str(_first(raw, _STATUS) or "").strip().lower()
    if any(k in text for k in ("fail", "reject", "declin", "cancel")):
        return "failed"
    if any(k in text for k in ("pend", "process", "initiat", "progress")):
        return "pending"
    return "confirmed"  # a posted statement row; absent status is not invented as failed


def normalize_wallet_transaction(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One statement row -> `ShiprocketWalletTransaction` columns, or None
    when date, amount or direction cannot be read (never guessed)."""
    when = _dt(_first(raw, _DATE))
    amount = _num(_first(raw, _AMOUNT))
    direction = _direction(raw)
    if amount is None:
        amount = _num(raw.get("credit")) or _num(raw.get("debit"))
    if when is None or amount is None or direction is None:
        return None
    amount = abs(amount)
    desc = _first(raw, _DESC)
    desc_text = str(desc) if desc is not None else ""
    hint = f"{desc_text} {_first(raw, _DIR) or ''} {raw.get('category') or ''}"
    ref = _first(raw, _REF)
    awb = _first(raw, _AWB)
    order = _first(raw, _ORDER)
    stable = (
        f"ref:{ref}:{direction}:{amount}"
        if ref is not None
        else "h:"
        + hashlib.sha256(
            f"{when.isoformat()}|{direction}|{amount}|{awb}|{order}|{desc_text}".encode()
        ).hexdigest()
    )
    return {
        "dedup_key": stable[:128],
        "transaction_ref": str(ref)[:128] if ref is not None else None,
        "txn_date": when,
        "txn_type": classify(direction, hint),
        "direction": direction,
        "amount": amount,
        "balance_after": _num(_first(raw, _BAL)),
        "order_number": str(order)[:64] if order is not None else None,
        "awb": str(awb)[:64] if awb is not None else None,
        "courier_name": (str(c)[:128] if (c := _first(raw, _COURIER)) is not None else None),
        "description": desc_text or None,
        "payment_reference": (str(p)[:128] if (p := _first(raw, _PAYREF)) is not None else None),
        "status": _status(raw),
    }


def now_utc() -> datetime:
    return datetime.now(UTC)
