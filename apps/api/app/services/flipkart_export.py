"""Excel export for a fetched Flipkart orders page (in memory; nothing saved)."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook

from app.schemas.flipkart_sales import FlipkartOrdersPage

UNAVAILABLE = "Unavailable"


def _s(v):
    """Neutralise spreadsheet formula injection from provider-supplied text."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def _n(v):
    return UNAVAILABLE if v is None else (float(v) if hasattr(v, "quantize") else v)


def page_to_xlsx(page: FlipkartOrdersPage) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(
        [
            "Order ID",
            "Shipment ID",
            "Order date",
            "Status",
            "Product name",
            "SKU",
            "FSN",
            "Quantity",
            "Selling price",
            "Payment type",
            "Tracking ID",
        ]
    )
    for it in page.items:
        ws.append(
            [
                it.order_id,
                it.shipment_id,
                it.order_date,
                it.status,
                _s(it.product_name) if it.product_name else UNAVAILABLE,
                _s(it.sku) or UNAVAILABLE,
                it.fsn or UNAVAILABLE,
                it.quantity,
                _n(it.selling_price),
                UNAVAILABLE,
                UNAVAILABLE,
            ]
        )

    meta = wb.create_sheet("Notes")
    meta.append(("Fetched at (UTC)", page.fetched_at.isoformat()))
    meta.append(("Date from", page.date_from.isoformat() if page.date_from else ""))
    meta.append(("Date to", page.date_to.isoformat() if page.date_to else ""))
    meta.append(("Has more pages", page.has_more))
    for w in page.warnings:
        meta.append(("Warning", _s(w)))
    for k, v in page.unavailable.items():
        meta.append((f"Unavailable: {k}", v))

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
