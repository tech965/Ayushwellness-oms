"""Marketplace Orders module — Phase 1.

A dedicated, read-only workspace for orders ingested from Amazon,
Flipkart, and Meesho — all three are `Order` rows (same table Shopify
orders use, see `app.models.mixins.SourceSystem`), distinguished purely by
`source_system`, so every existing inventory/shipment/fulfillment
workflow already applies to them unchanged. This module never touches,
imports from, or overlaps with `app/api/v1/endpoints/orders.py` (the
Shopify-facing Orders section) beyond re-filtering the exact same
`Order` table.

Phase 1 is read-only (list + dashboard) against whatever marketplace
orders already exist in the `orders` table — there is no live sync yet
(`app.integrations.amazon`/`flipkart`/`meesho` are stub adapters), so an
empty list/zeroed dashboard here is expected, not a bug, until a later
phase wires real credentials.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.orders import _to_list_response
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.models.enums import (
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
    ShipmentStatus,
)
from app.models.mixins import SourceSystem
from app.models.order import Order
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.marketplace import MarketplaceDashboardSummaryResponse
from app.schemas.order import MarketplaceOrderListResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.analytics_service import DateRange, _kpi, _previous_range, resolve_range
from app.services.order_service import OrderService

router = APIRouter()

# Every `Order.source_system` value this module's "Marketplace" filter
# covers — the single place that list is defined, so `/orders` and
# `/dashboard/summary` can never drift apart on which marketplaces count.
MARKETPLACE_SOURCE_SYSTEMS: list[str] = [
    SourceSystem.AMAZON,
    SourceSystem.FLIPKART,
    SourceSystem.MEESHO,
]


def _marketplace_order_filters(
    marketplace: str | None = Query(
        default=None,
        description="One of amazon/flipkart/meesho — omit for all three.",
    ),
    status: OrderStatus | None = Query(default=None, description="Order status filter."),
    payment_status: PaymentStatus | None = Query(default=None),
    fulfillment_status: FulfillmentStatus | None = Query(default=None),
    shipment_status: ShipmentStatus | None = Query(default=None),
    sku: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    q: str | None = Query(
        default=None,
        description=(
            "Searches order number/Shopify id, customer name/phone/email, and "
            "shipment tracking number — covers both the 'Customer Name' and "
            "'Order ID' filters from the same box, same convention the main "
            "Orders page's search already uses."
        ),
    ),
) -> dict:
    return {
        "source_systems": [marketplace] if marketplace else MARKETPLACE_SOURCE_SYSTEMS,
        "status": status,
        "payment_status": payment_status,
        "fulfillment_status": fulfillment_status,
        "shipment_status": shipment_status,
        "sku": sku,
        "date_from": date_from,
        "date_to": date_to,
        "q": q,
    }


def _to_marketplace_list_response(order: Order) -> MarketplaceOrderListResponse:
    """`_to_list_response` (orders.py) already denormalizes customer/
    product/shipment columns from the same eager-loaded relationships
    `OrderRepository.search_query` loads — reused unchanged, just
    widened with the 3 marketplace-specific fields `OrderListResponse`
    doesn't need for Shopify orders.
    """
    base = _to_list_response(order)
    items = order.items
    sku_summary: str | None = None
    if items:
        first_sku = items[0].sku
        sku_summary = first_sku if len(items) == 1 else f"{first_sku} +{len(items) - 1} more"
    # `base.model_dump()` already includes `source_system` (a plain
    # `OrderResponse` field) -- only `external_id`/`sku_summary` are new.
    return MarketplaceOrderListResponse(
        **base.model_dump(),
        external_id=order.external_id,
        sku_summary=sku_summary,
    )


@router.get("/orders", response_model=PaginatedResponse[MarketplaceOrderListResponse])
async def list_marketplace_orders(
    filters: dict = Depends(_marketplace_order_filters),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("marketplace.read")),
) -> PaginatedResponse[MarketplaceOrderListResponse]:
    items, total = await OrderService(session).list_orders(
        page_params=page_params, sort_params=sort_params, **filters
    )
    return PaginatedResponse(
        data=[_to_marketplace_list_response(o) for o in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


async def _marketplace_counts(session: AsyncSession, r: DateRange) -> dict[str, Decimal]:
    """One conditional-aggregation query, mirroring `AnalyticsService.
    _summary_counts`'s own "`func.count(case(...))` per metric" pattern
    (see that method's docstring for why this is safe/equivalent to N
    separate queries) — kept as its own standalone query here rather than
    extending `_summary_counts` itself, so the main OMS dashboard's
    finely-tuned query is never touched by this module.
    """

    def _count_if(condition):  # noqa: ANN001, ANN202
        return func.count(case((condition, 1)))

    def _sum_if(condition):  # noqa: ANN001, ANN202
        return func.coalesce(func.sum(case((condition, Order.total_amount))), 0)

    row = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Order.total_amount), 0),
                _count_if(Order.source_system == SourceSystem.AMAZON),
                _count_if(Order.source_system == SourceSystem.FLIPKART),
                _count_if(Order.source_system == SourceSystem.MEESHO),
                _count_if(Order.status == OrderStatus.PENDING),
                _count_if(Order.fulfillment_status == FulfillmentStatus.UNFULFILLED),
                _count_if(Order.status == OrderStatus.SHIPPED),
                _count_if(Order.status == OrderStatus.DELIVERED),
                _count_if(Order.status == OrderStatus.CANCELLED),
                _count_if(Order.payment_type == PaymentType.COD),
                _count_if(Order.payment_type == PaymentType.PREPAID),
            ).where(
                Order.source_system.in_(MARKETPLACE_SOURCE_SYSTEMS),
                Order.order_datetime >= r.date_from,
                Order.order_datetime <= r.date_to,
            )
        )
    ).one()
    (
        total_orders,
        total_sales,
        amazon_orders,
        flipkart_orders,
        meesho_orders,
        pending_orders,
        unfulfilled_orders,
        shipped_orders,
        delivered_orders,
        cancelled_orders,
        cod_orders,
        prepaid_orders,
    ) = (Decimal(v) if v is not None else Decimal("0") for v in row)
    return {
        "total_orders": total_orders,
        "total_sales": total_sales,
        "amazon_orders": amazon_orders,
        "flipkart_orders": flipkart_orders,
        "meesho_orders": meesho_orders,
        "pending_orders": pending_orders,
        "unfulfilled_orders": unfulfilled_orders,
        "shipped_orders": shipped_orders,
        "delivered_orders": delivered_orders,
        "cancelled_orders": cancelled_orders,
        "cod_orders": cod_orders,
        "prepaid_orders": prepaid_orders,
    }


@router.get("/dashboard/summary", response_model=ApiResponse[MarketplaceDashboardSummaryResponse])
async def get_marketplace_dashboard_summary(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("marketplace.read")),
) -> ApiResponse[MarketplaceDashboardSummaryResponse]:
    """Same current-vs-previous-period KPI shape as the main OMS
    dashboard (`GET /analytics/summary`), scoped to `source_system IN
    (amazon, flipkart, meesho)` — reuses `resolve_range`/`_previous_range`/
    `_kpi` from `app.services.analytics_service` unchanged so the two
    dashboards' date-window/percent-change semantics never drift apart.
    """
    current = resolve_range(date_from, date_to)
    previous = _previous_range(current)

    current_counts = await _marketplace_counts(session, current)
    previous_counts = await _marketplace_counts(session, previous)

    return ApiResponse(
        data=MarketplaceDashboardSummaryResponse(
            date_from=current.date_from,
            date_to=current.date_to,
            **{key: _kpi(current_counts[key], previous_counts[key]) for key in current_counts},
        )
    )
