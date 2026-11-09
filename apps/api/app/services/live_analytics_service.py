"""Live analytics over Shopify orders + internal workflow (no provider
data stored). Every figure is computed in memory from a bounded live
collection; `complete=False` means the totals cover only the orders
scanned (see `warnings`) and must not be read as global totals."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import to_ist
from app.integrations.shopify.live import ShopifyLiveReader
from app.models.enums import AssignmentStatus, OrderStatus
from app.models.telecalling import OrderAssignment
from app.services.live_collect import collect_orders
from app.services.order_ref import ref_id_for


class LabelCount(BaseModel):
    label: str
    count: int
    amount: Decimal = Decimal("0")


class DailyPoint(BaseModel):
    date: str
    orders: int
    revenue: Decimal


class ProductSales(BaseModel):
    sku: str
    product_name: str
    quantity: int
    revenue: Decimal


class LiveAnalyticsSummary(BaseModel):
    complete: bool
    orders_scanned: int
    warnings: list[str]
    total_orders: int
    cancelled_orders: int
    revenue: Decimal
    average_order_value: Decimal
    by_fulfillment_status: list[LabelCount]
    by_payment_type: list[LabelCount]
    by_payment_status: list[LabelCount]
    by_shopify_delivery_status: list[LabelCount]
    daily: list[DailyPoint]
    top_products: list[ProductSales]
    confirmed_orders: int
    assigned_orders: int
    unassigned_orders: int
    # Not derivable from Shopify; present so the UI can say so explicitly.
    unavailable: list[str]


def _bucket(orders: list[dict[str, Any]], key) -> list[LabelCount]:  # noqa: ANN001
    counts: dict[str, list] = defaultdict(lambda: [0, Decimal("0")])
    for o in orders:
        label = key(o)
        counts[label][0] += 1
        counts[label][1] += o["total_amount"]
    return [
        LabelCount(label=k, count=v[0], amount=v[1])
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1][0])
    ]


async def build_summary(
    session: AsyncSession,
    reader: ShopifyLiveReader,
    *,
    date_from: datetime | None,
    date_to: datetime | None,
    max_pages: int,
    top_n: int = 10,
) -> LiveAnalyticsSummary:
    collected = await collect_orders(
        session, reader, date_from=date_from, date_to=date_to, max_pages=max_pages
    )
    orders = collected.orders
    live_orders = [o for o in orders if not o.get("is_cancelled")]
    revenue = sum((o["total_amount"] for o in live_orders), Decimal("0"))

    daily: dict[str, list] = defaultdict(lambda: [0, Decimal("0")])
    products: dict[tuple[str, str], list] = defaultdict(lambda: [0, Decimal("0")])
    for o in live_orders:
        day = to_ist(o["order_datetime"]).date().isoformat()
        daily[day][0] += 1
        daily[day][1] += o["total_amount"]
        for item in o["items"]:
            entry = products[(item["sku"], item["product_name"])]
            entry[0] += item["quantity"]
            entry[1] += item["total_amount"]

    ref_ids = [
        ref_id_for(o["external_id"], collected.workflows.get(o["external_id"])) for o in orders
    ]
    assigned_ids: set = set()
    for start in range(0, len(ref_ids), 500):
        batch = ref_ids[start : start + 500]
        assigned_ids.update(
            (
                await session.execute(
                    select(OrderAssignment.order_id).where(
                        OrderAssignment.order_id.in_(batch),
                        OrderAssignment.assignment_status == AssignmentStatus.ACTIVE,
                    )
                )
            )
            .scalars()
            .all()
        )
    confirmed = sum(
        1
        for o in orders
        if (w := collected.workflows.get(o["external_id"])) is not None
        and w.status == OrderStatus.CONFIRMED
    )

    return LiveAnalyticsSummary(
        complete=not collected.incomplete,
        orders_scanned=len(orders),
        warnings=collected.warnings,
        total_orders=len(orders),
        cancelled_orders=len(orders) - len(live_orders),
        revenue=revenue,
        average_order_value=(revenue / len(live_orders)) if live_orders else Decimal("0"),
        by_fulfillment_status=_bucket(live_orders, lambda o: o["fulfillment_status"].value),
        by_payment_type=_bucket(live_orders, lambda o: o["payment_type"].value),
        by_payment_status=_bucket(live_orders, lambda o: o["payment_status"].value),
        by_shopify_delivery_status=_bucket(
            live_orders, lambda o: o.get("shopify_shipment_status") or "not_shipped"
        ),
        daily=[DailyPoint(date=d, orders=v[0], revenue=v[1]) for d, v in sorted(daily.items())],
        top_products=[
            ProductSales(sku=k[0], product_name=k[1], quantity=v[0], revenue=v[1])
            for k, v in sorted(products.items(), key=lambda kv: -kv[1][0])[:top_n]
        ],
        confirmed_orders=confirmed,
        assigned_orders=sum(1 for r in ref_ids if r in assigned_ids),
        unassigned_orders=sum(1 for r in ref_ids if r not in assigned_ids),
        unavailable=[
            "Carrier-level shipment status, courier performance, NDR and RTO "
            "(Shiprocket live reads are not implemented yet).",
            "Cashfree payment, refund and settlement detail "
            "(Cashfree live reads are not implemented yet).",
        ],
    )
