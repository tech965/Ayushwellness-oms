"""Tool (function) schemas advertised to the LLM.

These are the ONLY operations the model can invoke. Each maps 1:1 to a
handler in `chat.tool_runner.ToolRunner`, which runs an existing OMS
service method and returns already-formatted numbers.

Descriptions are kept terse on purpose: the whole schema list is resent
on every request, so verbosity here is a per-call token cost (and Groq's
free tier has a low tokens-per-minute limit).
"""

from __future__ import annotations

from typing import Any

from app.chat.datetime_ranges import COMMON_PRESETS

_DATE_PROPS: dict[str, Any] = {
    "period": {
        "type": "string",
        "enum": list(COMMON_PRESETS),
        "description": "Relative range, resolved in IST. Use for 'today', 'yesterday', etc.",
    },
    "date_from": {
        "type": "string",
        "description": "Explicit start (YYYY-MM-DD/ISO, IST). Overrides period.",
    },
    "date_to": {"type": "string", "description": "Explicit inclusive end (YYYY-MM-DD/ISO, IST)."},
}

_ORDER_STATUS = [
    "pending",
    "confirmed",
    "processing",
    "packed",
    "shipped",
    "delivered",
    "cancelled",
]
_SHIPMENT_STATUS = [
    "pending",
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "ndr",
    "rto_initiated",
    "rto_delivered",
    "cancelled",
]


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "get_operations_summary",
        "Headline KPIs for a period + the previous period and % change: orders, revenue, "
        "COD/prepaid counts+values+share, pending/fulfilled/unfulfilled orders, "
        "delivered/in-transit/out-for-delivery/delayed shipments, open NDR, open RTO, returns, "
        "refunds. Use for order counts, revenue, COD %, 'summary of today', 'today vs yesterday'.",
        {
            **_DATE_PROPS,
            "compare_to_previous": {
                "type": "boolean",
                "description": "Include previous period + deltas. Default true.",
            },
        },
    ),
    _tool(
        "get_orders_breakdown",
        "Order counts grouped by order status, payment type, payment status, fulfillment status, "
        "and current shipment status. Use for 'how many cancelled', 'pending fulfillment', "
        "'paid vs pending'.",
        {**_DATE_PROPS},
    ),
    _tool(
        "list_orders",
        "Look up individual orders matching filters; returns a total count + a capped sample. Use "
        "when the user wants to SEE specific orders, not just a number.",
        {
            **_DATE_PROPS,
            "status": {"type": "string", "enum": _ORDER_STATUS},
            "payment_type": {"type": "string", "enum": ["cod", "prepaid", "other"]},
            "fulfillment_status": {
                "type": "string",
                "enum": ["unfulfilled", "partial", "fulfilled"],
            },
            "shipment_status": {"type": "string", "enum": _SHIPMENT_STATUS},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "description": "Sample size. Default 10.",
            },
        },
    ),
    _tool(
        "get_top_products",
        "Best-selling products in a period by units sold (with revenue).",
        {
            **_DATE_PROPS,
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "Default 5."},
        },
    ),
    _tool(
        "get_courier_performance",
        "Per-courier shipment totals with delivered / NDR / RTO counts and percentages. Use for "
        "'which courier has the highest RTO / most delayed shipments'.",
        {
            **_DATE_PROPS,
            "sort_by": {
                "type": "string",
                "enum": [
                    "shipments",
                    "rto_pct",
                    "ndr_pct",
                    "delivered_pct",
                    "rto_count",
                    "ndr_count",
                ],
                "description": "Default 'shipments'.",
            },
        },
    ),
    _tool(
        "get_orders_timeseries",
        "Order count and revenue bucketed by day/week/month — for trends.",
        {
            **_DATE_PROPS,
            "interval": {
                "type": "string",
                "enum": ["day", "week", "month"],
                "description": "Default 'day'.",
            },
        },
    ),
    _tool(
        "compare_periods",
        "Headline KPIs for two periods side by side with % change. "
        "Use for 'this week vs last week'.",
        {
            "period_a": {
                "type": "object",
                "description": "Baseline.",
                "properties": dict(_DATE_PROPS),
            },
            "period_b": {
                "type": "object",
                "description": "Comparison.",
                "properties": dict(_DATE_PROPS),
            },
        },
        required=["period_a", "period_b"],
    ),
    _tool(
        "get_data_freshness",
        "When OMS data was last synced from Shopify and Shiprocket.",
        {},
    ),
]

TOOL_NAMES: frozenset[str] = frozenset(t["function"]["name"] for t in TOOLS)
