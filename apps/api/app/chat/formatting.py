"""Output formatting helpers for the assistant.

The frontend renders the model's prose as-is, so amounts and counts must
already be human-readable *in the Indian convention* (lakh/crore digit
grouping, `₹` prefix) by the time they reach the model as tool results —
the model is explicitly instructed to quote tool numbers verbatim rather
than reformat them.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

Number = int | float | Decimal | str


def _to_decimal(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def group_indian(value: Number) -> str:
    """`1234567.5` -> `12,34,567.5`. Grouping only; no currency symbol."""
    d = _to_decimal(value)
    sign = "-" if d < 0 else ""
    d = abs(d)
    whole = int(d)
    frac = d - whole
    digits = str(whole)
    if len(digits) <= 3:
        grouped = digits
    else:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    if frac:
        # Trim trailing zeros but keep at least the significant paise.
        frac_str = f"{frac:.2f}".split(".")[1].rstrip("0")
        if frac_str:
            grouped = f"{grouped}.{frac_str}"
    return f"{sign}{grouped}"


def rupees(value: Number, *, paise: bool = False) -> str:
    """`384620` -> `₹3,84,620`. With `paise=True`, always show 2 dp."""
    d = _to_decimal(value)
    if paise:
        sign = "-" if d < 0 else ""
        whole, frac = divmod(abs(d), 1)
        return f"{sign}₹{group_indian(int(whole))}.{int((frac * 100).to_integral_value()):02d}"
    return f"₹{group_indian(d.quantize(Decimal(1)) if d == d.to_integral_value() else d)}"


def count(value: Number) -> str:
    """Integer with Indian grouping: `4281` -> `4,281`."""
    return group_indian(int(_to_decimal(value)))


def percent(part: Number, whole: Number, *, digits: int = 1) -> str:
    p, w = _to_decimal(part), _to_decimal(whole)
    if w == 0:
        return "0%"
    return f"{(p / w * 100):.{digits}f}%"


def ratio_percent(value: Number, *, digits: int = 1) -> str:
    """Format an already-computed 0-100 percentage: `41.13` -> `41.1%`."""
    return f"{_to_decimal(value):.{digits}f}%"


def change(current: Number, previous: Number, *, digits: int = 1) -> str | None:
    """Signed period-over-period change, e.g. `+8.4%`. `None` when the
    baseline is zero (an undefined change, not an infinite one) — the
    caller should render that as "new" / "n/a".
    """
    c, p = _to_decimal(current), _to_decimal(previous)
    if p == 0:
        return None
    delta = (c - p) / p * 100
    return f"{'+' if delta >= 0 else ''}{delta:.{digits}f}%"


def money_number(value: Number) -> float:
    """Raw float for the machine-readable `data` block (not for prose)."""
    return float(_to_decimal(value))
