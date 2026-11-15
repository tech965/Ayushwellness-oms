"""IMS reports (JSON rows + Excel export). Stock reports are computed from
the ledger, so "as of <date>" is a true historical figure."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.core.exceptions import ValidationError
from app.core.timezone import ist_today
from app.services.ims_service import IMSService

REPORTS = ("stock", "movements", "transfers", "returns", "adjustments")
STOCK_VIEWS = ("warehouse", "product", "sku", "packaging", "category")
EXPORT_CAP = 5000

_MOVE_LABEL = {
    "opening_balance": "Opening balance",
    "inward": "Stock inward",
    "outward": "Stock outward",
    "transfer_out": "Transfer out",
    "transfer_in": "Transfer in",
    "return_in": "External return",
    "adjustment_in": "Adjustment (+)",
    "adjustment_out": "Adjustment (−)",
    "damaged": "Damaged",
    "missing": "Missing",
}


def _unit(item: dict[str, Any]) -> str:
    return item["unit"] or "UNITS"


def _product_name(item: dict[str, Any]) -> str:
    return item["name"].split(" — ")[0]


async def build_report(
    svc: IMSService,
    name: str,
    *,
    view: str = "warehouse",
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    category: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    if name not in REPORTS:
        raise ValidationError(f"Unknown report '{name}'.")
    whs = await svc.list_warehouses()
    wh_label = "All warehouses"
    if warehouse_id:
        match = next((w for w in whs if w["id"] == str(warehouse_id)), None)
        wh_label = f"{match['name']} ({match['code']})" if match else str(warehouse_id)

    if name == "stock":
        if view not in STOCK_VIEWS:
            raise ValidationError(f"Unknown stock view '{view}'.")
        as_of_date = as_of or ist_today()
        kind = {"sku": "finished", "packaging": "packaging"}.get(view)
        rows = await svc.stock_as_of(
            as_of_date, warehouse_id=warehouse_id, kind=kind, category=category, q=q
        )
        subtitle = f"Stock as of {as_of_date:%d-%m-%Y} (end of day, IST) · {wh_label}"
        if view in ("warehouse", "sku", "packaging"):
            columns = ["Warehouse", "Code", "Item", "SKU", "Category", "Type", "Unit", "Quantity"]
            data = [
                [r["warehouse"]["name"], r["warehouse"]["code"], r["item"]["name"],
                 r["item"]["sku"] or "", r["item"]["category"] or "", r["item"]["kind"],
                 _unit(r["item"]), r["quantity"]]
                for r in rows
            ]
            if view == "warehouse":
                data.sort(key=lambda x: (x[1], x[5], x[2]))
        else:
            codes = [w["code"] for w in whs if not warehouse_id or w["id"] == str(warehouse_id)]
            groups: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for r in rows:
                it = r["item"]
                key = (
                    (_product_name(it), it["kind"], _unit(it))
                    if view == "product"
                    else (it["category"] or "Uncategorised", it["kind"], _unit(it))
                )
                groups[key][r["warehouse"]["code"]] += r["quantity"]
            columns = ["Product" if view == "product" else "Category", "Type", "Unit", *codes, "Total"]
            data = [
                [k[0], k[1], k[2], *[g.get(c, 0) for c in codes], sum(g.values())]
                for k, g in sorted(groups.items())
            ]
        return {"name": name, "title": f"Stock report — {view}", "subtitle": subtitle,
                "columns": columns, "rows": data}

    if name == "movements":
        res = await svc.list_ledger(
            warehouse_id=warehouse_id, category=category, q=q, date_from=date_from,
            date_to=date_to, page=1, page_size=EXPORT_CAP,
        )
        return {
            "name": name, "title": "Stock movement report",
            "subtitle": _range(date_from, date_to, wh_label),
            "truncated": res["total_items"] > EXPORT_CAP,
            "columns": ["Date/time (UTC)", "Type", "Warehouse", "Code", "Item", "SKU", "Unit",
                        "Change", "Before", "After", "Reference", "User", "Notes"],
            "rows": [
                [m["occurred_at"], _MOVE_LABEL.get(m["movement_type"], m["movement_type"]),
                 m["warehouse"]["name"], m["warehouse"]["code"], m["item"]["name"],
                 m["item"]["sku"] or "", _unit(m["item"]), m["quantity_delta"],
                 m["quantity_before"], m["quantity_after"], m["reference_no"] or "",
                 m["actor"] or "", m["notes"] or ""]
                for m in res["items"]
            ],
        }

    if name == "transfers":
        res = await svc.list_transfers(
            warehouse_id=warehouse_id, q=q, date_from=date_from, date_to=date_to, page=1,
            page_size=EXPORT_CAP,
        )
        rows = [
            [t["created_at"], t["reference_no"], f"{t['source']['name']} ({t['source']['code']})",
             f"{t['destination']['name']} ({t['destination']['code']})", l["item"]["name"],
             l["item"]["sku"] or "", _unit(l["item"]), l["quantity"], t["created_by"] or "",
             t["notes"] or ""]
            for t in res["items"] for l in t["lines"]
        ]
        return {
            "name": name, "title": "Stock transfer report",
            "subtitle": _range(date_from, date_to, wh_label),
            "truncated": res["total_items"] > EXPORT_CAP,
            "columns": ["Date/time (UTC)", "Reference", "From", "To", "Item", "SKU", "Unit",
                        "Quantity", "User", "Notes"],
            "rows": rows,
        }

    if name == "returns":
        res = await svc.list_returns(
            warehouse_id=warehouse_id, date_from=date_from, date_to=date_to, page=1,
            page_size=EXPORT_CAP,
        )
        rows = [
            [r["return_date"], r["reference_no"], r["party"]["name"] if r["party"] else "",
             f"{r['warehouse']['name']} ({r['warehouse']['code']})", l["item"]["name"],
             l["item"]["sku"] or "", _unit(l["item"]), l["quantity"], r["document_no"] or "",
             r["created_by"] or "", r["notes"] or ""]
            for r in res["items"] for l in r["lines"]
        ]
        return {
            "name": name, "title": "External return report",
            "subtitle": _range(date_from, date_to, wh_label),
            "truncated": res["total_items"] > EXPORT_CAP,
            "columns": ["Return date", "Reference", "Party", "Receiving warehouse", "Item", "SKU",
                        "Unit", "Quantity", "Document no.", "User", "Notes"],
            "rows": rows,
        }

    res = await svc.list_adjustments(
        warehouse_id=warehouse_id, date_from=date_from, date_to=date_to, page=1,
        page_size=EXPORT_CAP,
    )
    return {
        "name": name, "title": "Stock adjustment report",
        "subtitle": _range(date_from, date_to, wh_label),
        "truncated": res["total_items"] > EXPORT_CAP,
        "columns": ["Date/time (UTC)", "Reference", "Warehouse", "Item", "SKU", "Unit", "Type",
                    "Direction", "Quantity", "Reason", "Notes", "User"],
        "rows": [
            [a["created_at"], a["reference_no"],
             f"{a['warehouse']['name']} ({a['warehouse']['code']})", a["item"]["name"],
             a["item"]["sku"] or "", _unit(a["item"]), a["adjustment_type"], a["direction"],
             a["quantity"], a["reason"], a["notes"] or "", a["created_by"] or ""]
            for a in res["items"]
        ],
    }


def _range(date_from: date | None, date_to: date | None, wh_label: str) -> str:
    lo = f"{date_from:%d-%m-%Y}" if date_from else "beginning"
    hi = f"{date_to:%d-%m-%Y}" if date_to else "today"
    return f"{lo} to {hi} · {wh_label}"


def to_xlsx(report: dict[str, Any]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = report["name"][:31]
    ws.append([report["title"]])
    ws.append([report["subtitle"]])
    ws.append([])
    ws.append(report["columns"])
    for cell in ws[4]:
        cell.font = Font(bold=True)
    ws["A1"].font = Font(bold=True, size=14)
    for row in report["rows"]:
        ws.append(row)
    for i, col in enumerate(report["columns"], start=1):
        width = max([len(str(col))] + [len(str(r[i - 1])) for r in report["rows"][:200]]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 60)
    ws.freeze_panes = "A5"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
