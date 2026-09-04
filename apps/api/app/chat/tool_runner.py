"""Executes one tool call from the model against a real OMS service.

Nothing here trusts the model: every date argument is re-resolved
server-side in IST, every tool is gated on the caller's RBAC permissions
(superusers excepted), and every returned number comes straight from an
existing, tested service method (`AnalyticsService`, `OrderService`, ...)
— this module never writes its own aggregation SQL for business metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import datetime_ranges as dr
from app.chat import formatting as fmt
from app.chat.datetime_ranges import DateRangeError, ResolvedRange
from app.core.logging import get_logger
from app.models.auth import User
from app.models.enums import SyncJobStatus
from app.models.integration import Integration, SyncJob
from app.schemas.common import PageParams, SortParams
from app.services.analytics_service import AnalyticsService
from app.services.order_service import OrderService

logger = get_logger(__name__)

_SRC_SHOPIFY = "OMS database (synced from Shopify)"
_SRC_LOGISTICS = "OMS database (synced from Shopify & Shiprocket)"
_SRC_SYNC = "OMS sync log"

# tool name -> permissions that grant it (any one is enough). Superusers
# bypass entirely. A caller missing all of these gets a structured
# "not_authorized" result the model relays politely.
_TOOL_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "get_operations_summary": ("analytics.read",),
    "get_orders_breakdown": ("analytics.read",),
    "get_top_products": ("analytics.read",),
    "get_courier_performance": ("analytics.read",),
    "get_orders_timeseries": ("analytics.read",),
    "compare_periods": ("analytics.read",),
    "list_orders": ("orders.read",),
    "get_data_freshness": ("analytics.read", "integrations.read", "sync_jobs.read"),
}


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    content: str  # JSON string handed back to the model
    sources: list[str] = field(default_factory=list)
    data: dict[str, Any] | None = None  # machine-readable, for the API response
    error_code: str | None = None


class ToolRunner:
    def __init__(self, session: AsyncSession, user: User, *, now: datetime | None = None) -> None:
        self.session = session
        self.user = user
        self._now = now
        self.analytics = AnalyticsService(session)

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name not in _TOOL_PERMISSIONS:
            return self._fail(name, "unknown_tool", f"No such tool: {name}.")

        if not self._authorized(name):
            return self._fail(
                name,
                "not_authorized",
                "You don't have permission to view this information in the OMS.",
            )

        handler = getattr(self, f"_{name}", None)
        if handler is None:  # pragma: no cover - guarded by _TOOL_PERMISSIONS
            return self._fail(name, "unknown_tool", f"No such tool: {name}.")

        try:
            return await handler(arguments or {})
        except DateRangeError as exc:
            return self._fail(name, "bad_date_range", str(exc))
        except Exception:  # noqa: BLE001 - surfaced as a safe message, logged with detail
            logger.exception("chat_tool_failed", tool=name, arguments=arguments)
            return self._fail(
                name,
                "tool_error",
                "The OMS returned an error while retrieving that data.",
            )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _authorized(self, name: str) -> bool:
        if self.user.is_superuser:
            return True
        allowed = _TOOL_PERMISSIONS[name]
        return any(code in self.user.permission_codes for code in allowed)

    def _ok(self, name: str, payload: dict[str, Any], sources: list[str]) -> ToolOutcome:
        body = {"ok": True, "tool": name, "sources": sources, **payload}
        return ToolOutcome(
            name=name,
            ok=True,
            content=json.dumps(body, default=_json_default),
            sources=sources,
            data=payload,
        )

    def _fail(self, name: str, code: str, message: str) -> ToolOutcome:
        body = {"ok": False, "tool": name, "error": code, "message": message}
        return ToolOutcome(
            name=name,
            ok=False,
            content=json.dumps(body),
            error_code=code,
        )

    def _range(self, args: dict[str, Any], *, default: str = "today") -> ResolvedRange:
        return dr.resolve(
            preset=args.get("period"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            now=self._now,
            default_preset=default,
        )

    # ------------------------------------------------------------------ #
    # tool handlers
    # ------------------------------------------------------------------ #
    async def _get_operations_summary(self, args: dict[str, Any]) -> ToolOutcome:
        rng = self._range(args)
        compare = args.get("compare_to_previous", True)
        summary = await self.analytics.get_summary(rng.date_from, rng.date_to)

        def kpi(name: str, *, money: bool = False) -> dict[str, Any]:
            k = getattr(summary, name)
            render = (lambda v: fmt.rupees(v)) if money else fmt.count
            out: dict[str, Any] = {"value": render(k.current), "raw": float(k.current)}
            if compare:
                out["previous"] = render(k.previous)
                out["change"] = fmt.change(k.current, k.previous)
            return out

        total_orders = Decimal(summary.total_orders.current)
        payload = {
            "period": rng.label,
            "date_from": rng.date_from.isoformat(),
            "date_to": rng.date_to.isoformat(),
            "compared_with": dr.previous_period(rng).label if compare else None,
            "orders": kpi("total_orders"),
            "revenue": kpi("total_revenue", money=True),
            "cod_orders": kpi("cod_orders"),
            "prepaid_orders": kpi("prepaid_orders"),
            "cod_value": kpi("cod_value", money=True),
            "prepaid_value": kpi("prepaid_value", money=True),
            "cod_share": fmt.percent(summary.cod_orders.current, total_orders),
            "prepaid_share": fmt.percent(summary.prepaid_orders.current, total_orders),
            "pending_orders": kpi("pending_orders"),
            "fulfilled_orders": kpi("fulfilled_orders"),
            "unfulfilled_orders": kpi("unfulfilled_orders"),
            "delivered_shipments": kpi("delivered_shipments"),
            "in_transit_shipments": kpi("in_transit_shipments"),
            "out_for_delivery_shipments": kpi("out_for_delivery_shipments"),
            "delayed_shipments": kpi("delayed_shipments"),
            "open_ndr": kpi("open_ndr"),
            "open_rto": kpi("open_rto"),
            "returns": kpi("returns"),
            "refunds": kpi("refunds"),
        }
        return self._ok("get_operations_summary", payload, [_SRC_LOGISTICS])

    async def _get_orders_breakdown(self, args: dict[str, Any]) -> ToolOutcome:
        rng = self._range(args)
        b = await self.analytics.get_breakdowns(rng.date_from, rng.date_to)

        def as_map(items: list[Any]) -> dict[str, int]:
            return {i.status: i.count for i in items}

        payload = {
            "period": rng.label,
            "date_from": rng.date_from.isoformat(),
            "date_to": rng.date_to.isoformat(),
            "by_order_status": as_map(b.order_status),
            "by_payment_type": as_map(b.payment_type),
            "by_payment_status": as_map(b.payment_status),
            "by_fulfillment_status": as_map(b.fulfillment_status),
            "by_shipment_status": as_map(b.shipment_status),
        }
        return self._ok("get_orders_breakdown", payload, [_SRC_LOGISTICS])

    async def _list_orders(self, args: dict[str, Any]) -> ToolOutcome:
        rng = self._range(args, default="today")
        limit = _clamp(args.get("limit", 10), 1, 25)
        filters = {
            key: args[key]
            for key in (
                "status",
                "payment_type",
                "payment_status",
                "fulfillment_status",
                "shipment_status",
            )
            if args.get(key)
        }
        orders, total = await OrderService(self.session).list_orders(
            page_params=PageParams(page=1, page_size=limit),
            sort_params=SortParams(sort_by="order_datetime", sort_order="desc"),
            date_from=rng.date_from,
            date_to=rng.date_to,
            **filters,
        )

        sample = []
        for o in orders:
            shipment = o.shipments[-1] if o.shipments else None
            sample.append(
                {
                    "order_number": o.order_number,
                    "placed_at": dr.to_ist(o.order_datetime).strftime("%d %b %Y, %I:%M %p IST"),
                    "amount": fmt.rupees(o.total_amount),
                    "order_status": o.status.value,
                    "payment_type": o.payment_type.value,
                    "payment_status": o.payment_status.value,
                    "fulfillment_status": o.fulfillment_status.value,
                    "shipment_status": shipment.current_status.value if shipment else None,
                    "courier": shipment.courier.name if shipment and shipment.courier else None,
                    "tracking": shipment.awb if shipment else None,
                    "customer": o.customer.full_name if o.customer else None,
                }
            )

        payload = {
            "period": rng.label,
            "filters": filters,
            "total_matching": total,
            "returned": len(sample),
            "truncated": total > len(sample),
            "orders": sample,
        }
        return self._ok("list_orders", payload, [_SRC_LOGISTICS])

    async def _get_top_products(self, args: dict[str, Any]) -> ToolOutcome:
        rng = self._range(args)
        limit = _clamp(args.get("limit", 5), 1, 25)
        products = await self.analytics.get_top_products(rng.date_from, rng.date_to, limit)
        payload = {
            "period": rng.label,
            "date_from": rng.date_from.isoformat(),
            "date_to": rng.date_to.isoformat(),
            "products": [
                {
                    "rank": i + 1,
                    "title": p.title,
                    "sku": p.sku,
                    "units_sold": p.units_sold,
                    "revenue": fmt.rupees(p.revenue),
                }
                for i, p in enumerate(products)
            ],
        }
        return self._ok("get_top_products", payload, [_SRC_SHOPIFY])

    async def _get_courier_performance(self, args: dict[str, Any]) -> ToolOutcome:
        rng = self._range(args, default="last_30_days")
        rows = await self.analytics.get_courier_performance(rng.date_from, rng.date_to)
        sort_by = args.get("sort_by", "shipments")
        key = {
            "shipments": lambda r: r.shipment_count,
            "rto_pct": lambda r: r.rto_pct,
            "ndr_pct": lambda r: r.ndr_pct,
            "delivered_pct": lambda r: r.delivered_pct,
            "rto_count": lambda r: r.rto_count,
            "ndr_count": lambda r: r.ndr_count,
        }.get(sort_by, lambda r: r.shipment_count)
        rows = sorted(rows, key=key, reverse=True)

        payload = {
            "period": rng.label,
            "date_from": rng.date_from.isoformat(),
            "date_to": rng.date_to.isoformat(),
            "sorted_by": sort_by,
            "couriers": [
                {
                    "name": r.name,
                    "shipments": r.shipment_count,
                    "delivered": r.delivered_count,
                    "delivered_pct": fmt.ratio_percent(r.delivered_pct),
                    "ndr": r.ndr_count,
                    "ndr_pct": fmt.ratio_percent(r.ndr_pct),
                    "rto": r.rto_count,
                    "rto_pct": fmt.ratio_percent(r.rto_pct),
                }
                for r in rows
            ],
        }
        return self._ok("get_courier_performance", payload, [_SRC_LOGISTICS])

    async def _get_orders_timeseries(self, args: dict[str, Any]) -> ToolOutcome:
        rng = self._range(args, default="last_7_days")
        interval = args.get("interval", "day")
        if interval not in ("day", "week", "month"):
            interval = "day"
        series = await self.analytics.get_orders_timeseries(rng.date_from, rng.date_to, interval)
        payload = {
            "period": rng.label,
            "interval": interval,
            "points": [
                {
                    "bucket": p.bucket,
                    "orders": p.order_count,
                    "revenue": fmt.rupees(p.revenue),
                    "revenue_raw": float(p.revenue),
                }
                for p in series.points
            ],
        }
        return self._ok("get_orders_timeseries", payload, [_SRC_SHOPIFY])

    async def _compare_periods(self, args: dict[str, Any]) -> ToolOutcome:
        a_args = args.get("period_a") or {}
        b_args = args.get("period_b") or {}
        if not _has_range(a_args) or not _has_range(b_args):
            return self._fail(
                "compare_periods",
                "bad_date_range",
                "Both period_a and period_b need a period or an explicit date range.",
            )
        rng_a = self._range(a_args, default="today")
        rng_b = self._range(b_args, default="yesterday")
        sum_a = await self.analytics.get_summary(rng_a.date_from, rng_a.date_to)
        sum_b = await self.analytics.get_summary(rng_b.date_from, rng_b.date_to)

        metrics = {
            "orders": ("total_orders", False),
            "revenue": ("total_revenue", True),
            "cod_orders": ("cod_orders", False),
            "prepaid_orders": ("prepaid_orders", False),
            "delivered_shipments": ("delivered_shipments", False),
            "open_ndr": ("open_ndr", False),
            "open_rto": ("open_rto", False),
        }
        rendered: dict[str, Any] = {}
        for label, (attr, money) in metrics.items():
            av = Decimal(getattr(sum_a, attr).current)
            bv = Decimal(getattr(sum_b, attr).current)
            render = (lambda v: fmt.rupees(v)) if money else fmt.count
            rendered[label] = {
                "period_a": render(av),
                "period_b": render(bv),
                "change_a_to_b": fmt.change(bv, av),
            }

        payload = {
            "period_a": {
                "label": rng_a.label,
                "date_from": rng_a.date_from.isoformat(),
                "date_to": rng_a.date_to.isoformat(),
            },
            "period_b": {
                "label": rng_b.label,
                "date_from": rng_b.date_from.isoformat(),
                "date_to": rng_b.date_to.isoformat(),
            },
            "metrics": rendered,
        }
        return self._ok("compare_periods", payload, [_SRC_LOGISTICS])

    async def _get_data_freshness(self, _args: dict[str, Any]) -> ToolOutcome:
        stmt = (
            select(Integration.name, SyncJob.entity_type, func.max(SyncJob.completed_at))
            .join(Integration, Integration.id == SyncJob.integration_id)
            .where(SyncJob.status == SyncJobStatus.COMPLETED)
            .group_by(Integration.name, SyncJob.entity_type)
        )
        rows = (await self.session.execute(stmt)).all()
        entries = [
            {
                "integration": name,
                "entity": entity_type,
                "last_synced": (
                    dr.to_ist(completed).strftime("%d %b %Y, %I:%M %p IST") if completed else None
                ),
                "last_synced_utc": completed.isoformat() if completed else None,
            }
            for name, entity_type, completed in rows
            if completed is not None
        ]
        payload = {
            "syncs": entries,
            "note": (
                "Orders/customers/products come from Shopify; shipments/NDR/RTO from Shiprocket."
                if entries
                else "No completed sync jobs recorded yet."
            ),
        }
        return self._ok("get_data_freshness", payload, [_SRC_SYNC])


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, n))


def _has_range(args: dict[str, Any]) -> bool:
    return bool(args.get("period") or args.get("date_from") or args.get("date_to"))
