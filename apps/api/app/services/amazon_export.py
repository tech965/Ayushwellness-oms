"""Excel export for the Amazon sales report (in memory; nothing saved)."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook

from app.schemas.amazon_sales import SalesReport

UNAVAILABLE = "Unavailable"


def _s(v):
    """Neutralise spreadsheet formula injection from provider-supplied text."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def _n(v):
    return UNAVAILABLE if v is None else (float(v) if hasattr(v, "quantize") else v)


def report_to_xlsx(report: SalesReport) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    s = report.summary
    for row in [
        ("Amazon Sales Analytics",),
        ("Period", f"{report.date_from} to {report.date_to}"),
        ("Fetched at (UTC)", report.fetched_at.isoformat()),
        ("Currency", s.currency or UNAVAILABLE),
        ("Total units ordered", s.total_units_ordered),
        ("Total ordered product sales", _n(s.total_ordered_revenue)),
        ("Products sold (distinct ASINs)", s.products_sold),
        ("Total units shipped", _n(s.total_units_shipped)),
        ("Total shipped product sales", _n(s.total_shipped_revenue)),
        ("Total units refunded", _n(s.total_units_refunded)),
        ("Note", "Sales revenue figures are not profit."),
    ]:
        ws.append(row)
    for w in report.warnings:
        ws.append(("Warning", _s(w)))

    d = wb.create_sheet("Daily")
    d.append(
        [
            "Date",
            "Units ordered",
            "Ordered sales",
            "Units shipped",
            "Shipped sales",
            "Units refunded",
        ]
    )
    for p in report.daily:
        d.append(
            [
                p.date.isoformat(),
                p.units_ordered,
                _n(p.ordered_revenue),
                _n(p.units_shipped),
                _n(p.shipped_revenue),
                _n(p.units_refunded),
            ]
        )

    for name, rows in (("Weekly", report.weekly), ("Monthly", report.monthly)):
        w = wb.create_sheet(name)
        w.append(
            [
                "Period",
                "Units ordered",
                "Ordered sales",
                "Units shipped",
                "Shipped sales",
                "Units refunded",
            ]
        )
        for p in rows:
            w.append(
                [
                    p.label,
                    p.units_ordered,
                    _n(p.ordered_revenue),
                    _n(p.units_shipped),
                    _n(p.shipped_revenue),
                    _n(p.units_refunded),
                ]
            )

    pr = wb.create_sheet("Products")
    pr.append(
        [
            "Product name",
            "Seller SKU",
            "ASIN",
            "Units ordered",
            "Units shipped",
            "Ordered sales",
            "Avg selling price",
        ]
    )
    for r in report.products:
        pr.append(
            [
                _s(r.product_name) if r.product_name else UNAVAILABLE,
                _s(r.sku) or UNAVAILABLE,
                r.asin,
                r.units_ordered,
                _n(r.units_shipped),
                _n(r.ordered_revenue),
                _n(r.avg_selling_price),
            ]
        )

    df = wb.create_sheet("Definitions")
    df.append(["Metric", "Definition"])
    for k, v in report.definitions.items():
        df.append([k, v])
    for k, v in report.unavailable.items():
        df.append([f"Unavailable: {k}", v])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
