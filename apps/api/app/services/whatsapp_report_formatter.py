"""Formats a `DailySalesReport` for WhatsApp text delivery and Excel
export. No image rendering here -- the spec's preferred delivery (an
approved WhatsApp media template with the report as an image) needs
both a real approved Meta template (doesn't exist yet) and an image-
rendering library this project doesn't currently depend on (Pillow
isn't installed) -- adding a new third-party dependency wasn't something
to do silently mid-task, so that path is deferred, not built. This
module's text output is what's actually sent today, as either a
free-form text message (inside the 24h window) or the body of an
approved text-only template (outside it).
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app.services.whatsapp_daily_report_service import DailySalesReport, PlatformRow

NA = "N/A"


def _money(v: Decimal | None) -> str:
    return NA if v is None else f"{v:,.2f}"


def _s(v):
    """Neutralise spreadsheet formula injection, same convention as
    `amazon_export.py`/`flipkart_export.py`.
    """
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def _n(v):
    return NA if v is None else (float(v) if hasattr(v, "quantize") else v)


def format_report_text(report: DailySalesReport) -> str:
    lines = [
        f"*AyushWellness — Daily Sales Report — {report.report_date.isoformat()}*",
        "",
    ]
    for p in report.platforms:
        if not p.configured:
            lines.append(f"{p.platform}: Not configured")
            continue
        lines.append(
            f"{p.platform}: {p.orders_count if p.orders_count is not None else NA} orders, "
            f"₹{_money(p.revenue)} revenue"
        )
    lines += [
        "",
        f"*Total*: {report.total_orders} orders, ₹{_money(report.total_revenue)} revenue",
        f"Total ad spend: {_money(report.total_ad_spend)}",
        f"Abandoned checkouts: {report.abandoned_count} (₹{_money(report.abandoned_revenue)})",
        f"Prepaid: {report.prepaid_orders}/{report.prepaid_eligible_orders}"
        + (f" ({report.prepaid_percent}%)" if report.prepaid_percent is not None else f" ({NA})"),
        "",
        "_Revenue figures are sales revenue, not profit._",
    ]
    if report.warnings:
        lines.append("")
        lines.append("_Notes:_")
        for w in report.warnings:
            lines.append(f"- {w}")
    return "\n".join(lines)


def _platform_sheet_row(p: PlatformRow) -> list:
    if not p.configured:
        return [p.platform, NA, NA, NA, NA, NA, NA, NA, p.note or NA]
    return [
        p.platform,
        _n(p.orders_count),
        _n(p.revenue),
        _n(p.revenue_from_ads),
        _n(p.smm_spent),
        _n(p.sem_spent),
        _n(p.roi_from_ads),
        _n(p.roas),
        _s(p.note) if p.note else "",
    ]


def report_to_xlsx(report: DailySalesReport) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Sales Report"
    ws.append(
        [
            "Platform",
            "Orders Count",
            "Revenue",
            "Revenue from Ecom.Ads",
            "SMM Spent",
            "SEM Spent",
            "ROI from Ecom.Ads",
            "ROAS",
            "Note",
        ]
    )
    for p in report.platforms:
        ws.append(_platform_sheet_row(p))
    ws.append(["Total", report.total_orders, _n(report.total_revenue), NA, NA, NA, NA, NA, ""])

    meta = wb.create_sheet("Summary")
    meta.append(("Report date (Asia/Kolkata)", report.report_date.isoformat()))
    meta.append(("Generated at (UTC)", report.generated_at.isoformat()))
    meta.append(("Total orders", report.total_orders))
    meta.append(("Total revenue", _n(report.total_revenue)))
    meta.append(("Total ad spend", _n(report.total_ad_spend)))
    meta.append(("Abandoned checkouts (count)", report.abandoned_count))
    meta.append(("Abandoned checkouts (revenue)", _n(report.abandoned_revenue)))
    meta.append(("Prepaid orders", report.prepaid_orders))
    meta.append(("Prepaid-eligible orders (Shopify only)", report.prepaid_eligible_orders))
    meta.append(("Prepaid %", _n(report.prepaid_percent)))
    meta.append(("Note", "Revenue figures are sales revenue, not profit."))
    for w in report.warnings:
        meta.append(("Warning", _s(w)))

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
