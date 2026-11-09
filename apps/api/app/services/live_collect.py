"""Bounded full collection of live Shopify orders (exports, analytics).

Every matching order, limited to `max_pages` Shopify pages. If the bound
is hit with more remaining, `incomplete` is True and a warning is
returned -- a partial result is never presented as complete. Nothing is
persisted; the only database access is reading `order_workflow`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.shopify.live import MAX_PAGE_SIZE, ShopifyLiveReader
from app.models.enums import OrderStatus
from app.models.order_workflow import OrderWorkflow
from app.services.live_order_service import _matches
from app.services.order_ref import get_workflow_by_external_ids

EXPORT_MAX_PAGES = 50  # x MAX_PAGE_SIZE = at most 5,000 orders per request


@dataclass
class CollectedOrders:
    orders: list[dict[str, Any]]
    workflows: dict[str, OrderWorkflow]
    incomplete: bool
    warnings: list[str] = field(default_factory=list)


async def collect_orders(
    session: AsyncSession,
    reader: ShopifyLiveReader,
    *,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    status: OrderStatus | None = None,
    payment_status: Any = None,
    payment_type: Any = None,
    fulfillment_status: Any = None,
    sku: str | None = None,
    tag: str | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    confirmed_only: bool = False,
    telecaller_id: Any = None,
    confirmed_date_from: datetime | None = None,
    confirmed_date_to: datetime | None = None,
    max_pages: int = EXPORT_MAX_PAGES,
    shopify_fulfillment_status: str | None = None,
) -> CollectedOrders:
    client_filters = {
        "status": status,
        "payment_status": payment_status,
        "payment_type": payment_type,
        "fulfillment_status": fulfillment_status,
        "sku": sku,
        "tag": tag,
        "amount_min": amount_min,
        "amount_max": amount_max,
    }
    warnings: list[str] = []
    matched: list[dict[str, Any]] = []
    workflows: dict[str, OrderWorkflow] = {}

    if confirmed_only or telecaller_id or confirmed_date_from or confirmed_date_to:
        conditions: list[Any] = []
        if confirmed_only:
            conditions.append(OrderWorkflow.confirmed_by_telecaller_id.is_not(None))
        if telecaller_id:
            conditions.append(OrderWorkflow.confirmed_by_telecaller_id == telecaller_id)
        if confirmed_date_from:
            conditions.append(OrderWorkflow.confirmed_at >= confirmed_date_from)
        if confirmed_date_to:
            conditions.append(OrderWorkflow.confirmed_at <= confirmed_date_to)
        cap = max_pages * MAX_PAGE_SIZE
        rows = list(
            (
                await session.execute(
                    select(OrderWorkflow)
                    .where(*conditions)
                    .order_by(OrderWorkflow.confirmed_at.desc().nulls_last())
                    .limit(cap + 1)
                )
            )
            .scalars()
            .all()
        )
        incomplete = len(rows) > cap
        rows = rows[:cap]
        workflows = {w.external_order_id: w for w in rows}
        for order in await reader.get_orders_by_ids(
            [w.external_order_id for w in rows], q=q, date_from=date_from, date_to=date_to
        ):
            if _matches(order, workflows.get(order["external_id"]), **client_filters):
                matched.append(order)
    else:
        cursor: str | None = None
        has_more = True
        pages = 0
        while has_more and pages < max_pages:
            page = await reader.list_orders(
                limit=MAX_PAGE_SIZE,
                cursor=cursor,
                q=q,
                date_from=date_from,
                date_to=date_to,
                fulfillment_status=shopify_fulfillment_status,
            )
            pages += 1
            cursor, has_more = page.next_cursor, page.has_more
            found = await get_workflow_by_external_ids(
                session, [o["external_id"] for o in page.items]
            )
            workflows.update(found)
            matched.extend(
                o for o in page.items if _matches(o, found.get(o["external_id"]), **client_filters)
            )
        incomplete = has_more

    if incomplete:
        warnings.append(
            f"Stopped after {max_pages} Shopify pages; results are incomplete. "
            "Narrow the date range or filters."
        )
    return CollectedOrders(
        orders=matched, workflows=workflows, incomplete=incomplete, warnings=warnings
    )
