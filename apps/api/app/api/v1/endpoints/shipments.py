"""Shipment CRUD, search/filter, timeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.live_mode import unavailable_in_live_mode_for
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.models.order import Order
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.shipment import (
    DeliveryPerformanceResponse,
    DeliveryTrendResponse,
    FulfilmentSummaryResponse,
    FulfilmentTrendResponse,
    PartnerAllocationResponse,
    PartnerPerformanceResponse,
    PerformanceOrderRowResponse,
    ShipmentAnalyticsResponse,
    ShipmentCreateRequest,
    ShipmentEventResponse,
    ShipmentListResponse,
    ShipmentQueueRowResponse,
    ShipmentResponse,
    ShipmentStatusAnalyticsResponse,
    ShipmentStatusDrilldownRowResponse,
    ShipmentStatusHistoryResponse,
    ShipmentSummaryResponse,
    ShipmentUpdateRequest,
)
from app.schemas.shiprocket import (
    LocateShiprocketOrderRequest,
    LocateShiprocketOrderResult,
    ShiprocketAssignAwbRequest,
)
from app.services.fulfilment_performance_service import FulfilmentPerformanceService
from app.services.performance_orders_service import (
    FULFILMENT_SCOPES,
    PARTNER_METRICS,
    PerformanceOrdersService,
)
from app.services.shipment_service import ShipmentService
from app.services.shipment_status import UNIFIED_STATUSES
from app.services.shipment_status_analytics_service import (
    KPI_KEYS,
    STATUS_GROUP_KEYS,
    ShipmentStatusAnalyticsService,
)
from app.services.shipping_partner_performance_service import ShippingPartnerPerformanceService
from app.services.shiprocket_service import (
    ShiprocketOperationsService,
    locate_shiprocket_orders,
    shiprocket_order_id,
)
from app.services.shopify_fulfillment_service import ShopifyFulfillmentService

router = APIRouter()

_TOTALS_UNAVAILABLE = (
    "Shipment totals need a bulk Shiprocket status API that this integration does not have."
)


def _to_shipment_queue_row(order: Order, shipment: Shipment | None) -> ShipmentQueueRowResponse:
    item_summary = ", ".join(f"{i.product_name} x{i.quantity}" for i in order.items) or None
    sku_summary = ", ".join(i.sku for i in order.items if i.sku) or None
    total_quantity = sum(i.quantity for i in order.items)
    return ShipmentQueueRowResponse(
        order_id=order.id,
        order_number=order.order_number,
        customer_name=order.customer.full_name if order.customer else None,
        customer_phone=order.customer.phone if order.customer else None,
        item_summary=item_summary,
        sku_summary=sku_summary,
        total_quantity=total_quantity,
        total_amount=order.total_amount,
        payment_type=order.payment_type,
        payment_status=order.payment_status,
        confirmed_at=order.confirmed_at,
        confirmed_by_telecaller_id=order.confirmed_by_telecaller_id,
        confirmed_by_telecaller_name=(
            order.confirmed_by_telecaller.name if order.confirmed_by_telecaller else None
        ),
        shipment_id=shipment.id if shipment else None,
        shipment_status=shipment.current_status if shipment else None,
        shopify_sync_status=shipment.shopify_sync_status if shipment else None,
        awb=shipment.awb if shipment else None,
        courier_name=shipment.courier.name if shipment and shipment.courier else None,
        shiprocket_order_id=shiprocket_order_id(shipment),
        shipping_address_validation_status=order.shipping_address_validation_status,
        shipping_address_validation_score=order.shipping_address_validation_score,
        shipping_address_validation_reason=order.shipping_address_validation_reason,
    )


@router.get("/queue", response_model=PaginatedResponse[ShipmentQueueRowResponse])
async def get_shipment_queue(
    q: str | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    telecaller_id: uuid.UUID | None = Query(default=None),
    courier_id: uuid.UUID | None = Query(default=None),
    sku: str | None = Query(default=None),
    shipment_status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> PaginatedResponse[ShipmentQueueRowResponse]:
    """Confirmed orders awaiting shipment processing — a distinct,
    narrower view from `GET /shipments` below (every already-processed
    shipment). Registered before `GET /{shipment_id}` so "queue" is never
    parsed as a shipment id.
    """
    rows, total = await ShipmentService(session).list_shipment_queue(
        page_params=page_params,
        q=q,
        payment_type=payment_type,
        telecaller_id=telecaller_id,
        courier_id=courier_id,
        sku=sku,
        shipment_status=shipment_status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[_to_shipment_queue_row(order, shipment) for order, shipment in rows],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get(
    "/summary",
    response_model=ApiResponse[ShipmentSummaryResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_shipment_summary(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[ShipmentSummaryResponse]:
    summary = await ShipmentService(session).get_summary()
    return ApiResponse(data=ShipmentSummaryResponse(**summary))


@router.get(
    "/analytics",
    response_model=ApiResponse[ShipmentAnalyticsResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_shipment_analytics(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[ShipmentAnalyticsResponse]:
    analytics = await ShipmentService(session).get_analytics(date_from=date_from, date_to=date_to)
    return ApiResponse(data=ShipmentAnalyticsResponse(**analytics))


def _status_analytics_filters(
    courier_id: uuid.UUID | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    sku: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict:
    return {
        "courier_id": courier_id,
        "payment_type": payment_type,
        "sku": sku,
        "date_from": date_from,
        "date_to": date_to,
    }


@router.get(
    "/status-analytics",
    response_model=ApiResponse[ShipmentStatusAnalyticsResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_shipment_status_analytics(
    filters: dict = Depends(_status_analytics_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[ShipmentStatusAnalyticsResponse]:
    """OMS SOW Module 2 — unified 12-status funnel breakdown plus the 5
    KPI percentages (Pickup/In-Transit/OFD/Delivery/RTO %), filterable
    by date range, shipping partner, COD/Prepaid and SKU. Registered
    before `GET /{shipment_id}` so "status-analytics" is never parsed as
    a shipment id, same as `/queue` above.
    """
    data = await ShipmentStatusAnalyticsService(session).get_status_analytics(**filters)
    return ApiResponse(data=ShipmentStatusAnalyticsResponse(**data))


@router.get(
    "/status-analytics/history",
    response_model=ApiResponse[ShipmentStatusHistoryResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_shipment_status_history(
    filters: dict = Depends(_status_analytics_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[ShipmentStatusHistoryResponse]:
    """Date-wise (IST) unified-status distribution — the SOW's
    "date-wise shipment status tracking" / "historical shipment status
    reports". Defaults to the last 30 days, same window as `GET
    /shipments/analytics`, when no range is given.
    """
    resolved_to = filters["date_to"] or datetime.now(UTC)
    resolved_from = filters["date_from"] or (resolved_to - timedelta(days=30))
    data = await ShipmentStatusAnalyticsService(session).get_status_history(
        date_from=resolved_from,
        date_to=resolved_to,
        courier_id=filters["courier_id"],
        payment_type=filters["payment_type"],
        sku=filters["sku"],
    )
    return ApiResponse(data=ShipmentStatusHistoryResponse(**data))


@router.get(
    "/status-analytics/history/export",
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def export_shipment_status_history(
    filters: dict = Depends(_status_analytics_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> StreamingResponse:
    resolved_to = filters["date_to"] or datetime.now(UTC)
    resolved_from = filters["date_from"] or (resolved_to - timedelta(days=30))
    workbook = await ShipmentStatusAnalyticsService(session).export_status_history_xlsx(
        date_from=resolved_from,
        date_to=resolved_to,
        courier_id=filters["courier_id"],
        payment_type=filters["payment_type"],
        sku=filters["sku"],
    )
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=shipment-status-history.xlsx"},
    )


@router.get(
    "/status-analytics/orders",
    response_model=PaginatedResponse[ShipmentStatusDrilldownRowResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_shipment_status_drilldown(
    unified_status: str | None = Query(default=None, pattern=f"^({'|'.join(UNIFIED_STATUSES)})$"),
    group: str | None = Query(default=None, pattern=f"^({'|'.join(STATUS_GROUP_KEYS)})$"),
    kpi: str | None = Query(default=None, pattern=f"^({'|'.join(KPI_KEYS)})$"),
    q: str | None = Query(default=None, max_length=100),
    filters: dict = Depends(_status_analytics_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> PaginatedResponse[ShipmentStatusDrilldownRowResponse]:
    """Individual orders behind a headline KPI (`kpi`), a management
    category (`group`) or one exact unified status (`unified_status`) --
    exactly one of the three. Same filters as the counts, so totals match.
    """
    if sum(v is not None for v in (unified_status, group, kpi)) != 1:
        raise HTTPException(
            status_code=422, detail="Provide exactly one of unified_status, group or kpi."
        )
    rows, total = await ShipmentStatusAnalyticsService(session).get_status_drilldown(
        unified_status=unified_status,
        group=group,
        kpi=kpi,
        q=q,
        page_params=page_params,
        **filters,
    )
    return PaginatedResponse(
        data=[ShipmentStatusDrilldownRowResponse(**row) for row in rows],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get(
    "/fulfilment-performance/orders",
    response_model=PaginatedResponse[PerformanceOrderRowResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_fulfilment_performance_orders(
    scope: str = Query(..., pattern=f"^({'|'.join(FULFILMENT_SCOPES)})$"),
    day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    q: str | None = Query(default=None, max_length=100),
    filters: dict = Depends(_status_analytics_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> PaginatedResponse[PerformanceOrderRowResponse]:
    """Orders behind a Module 3 KPI card, pending card or daily-chart bar,
    under the same filters as the aggregates (see
    `PerformanceOrdersService`'s docstring for the matching rules).
    """
    rows, total = await PerformanceOrdersService(session).fulfilment_orders(
        scope=scope, day=day, q=q, page_params=page_params, **filters
    )
    return PaginatedResponse(
        data=[PerformanceOrderRowResponse(**row) for row in rows],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get(
    "/fulfilment-performance",
    response_model=ApiResponse[FulfilmentSummaryResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_fulfilment_performance(
    filters: dict = Depends(_status_analytics_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[FulfilmentSummaryResponse]:
    """OMS SOW Module 3, Section A -- average fulfilment time, pending
    fulfilment counts (>24h/>48h), and the by-team/warehouse breakdown
    (always empty -- see `FulfilmentPerformanceService`'s module
    docstring for why that attribution genuinely doesn't exist in this
    data model). Reuses Module 2's filter set; `unified_status` isn't
    accepted here, this endpoint has its own status grouping.
    """
    data = await FulfilmentPerformanceService(session).get_fulfilment_summary(**filters)
    return ApiResponse(data=FulfilmentSummaryResponse(**data))


@router.get(
    "/fulfilment-performance/history",
    response_model=ApiResponse[FulfilmentTrendResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_fulfilment_performance_history(
    filters: dict = Depends(_status_analytics_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[FulfilmentTrendResponse]:
    """Date-wise (IST, bucketed by pickup day) average fulfilment time."""
    data = await FulfilmentPerformanceService(session).get_fulfilment_trend(**filters)
    return ApiResponse(data=FulfilmentTrendResponse(**data))


def _delivery_performance_filters(
    courier_id: uuid.UUID | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    sku: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    zone: str | None = Query(default=None, description="Indian state name (normalized server-side)."),
) -> dict:
    return {
        "courier_id": courier_id,
        "payment_type": payment_type,
        "sku": sku,
        "date_from": date_from,
        "date_to": date_to,
        "zone": zone,
    }


@router.get(
    "/delivery-performance",
    response_model=ApiResponse[DeliveryPerformanceResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_delivery_performance(
    filters: dict = Depends(_delivery_performance_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[DeliveryPerformanceResponse]:
    """OMS SOW Module 3, Section B -- average delivery days (pickup ->
    delivery, successfully-delivered shipments only -- see the service
    module's docstring for why RTO/cancelled shipments can't pollute
    this), overall and split by courier, zone (state-based -- see
    `zone_is_state_based`), and COD/Prepaid.
    """
    data = await FulfilmentPerformanceService(session).get_delivery_performance(**filters)
    return ApiResponse(data=DeliveryPerformanceResponse(**data))


@router.get(
    "/delivery-performance/history",
    response_model=ApiResponse[DeliveryTrendResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_delivery_performance_history(
    filters: dict = Depends(_status_analytics_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[DeliveryTrendResponse]:
    """Date-wise (IST, bucketed by delivery day) average delivery days --
    the SOW's "delivery performance trends".
    """
    data = await FulfilmentPerformanceService(session).get_delivery_trend(**filters)
    return ApiResponse(data=DeliveryTrendResponse(**data))


def _partner_filters(
    payment_type: str | None = Query(default=None),
    sku: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict:
    return {"payment_type": payment_type, "sku": sku, "date_from": date_from, "date_to": date_to}


@router.get(
    "/partner-allocation",
    response_model=ApiResponse[PartnerAllocationResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_partner_allocation(
    filters: dict = Depends(_partner_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[PartnerAllocationResponse]:
    """OMS SOW Module 4, Section A -- orders allocated and allocation %
    per registered shipping partner (percentages sum to exactly 100%),
    plus avg delivery days reused directly from Module 3. Only couriers
    with at least one allocated order appear; unintegrated partners
    (NimbusPost/Blue Dart/D2DC -- see Module 5) have no `Courier` row and
    so simply don't appear, rather than being shown with fabricated
    figures.
    """
    data = await ShippingPartnerPerformanceService(session).get_partner_allocation(**filters)
    return ApiResponse(data=PartnerAllocationResponse(**data))


@router.get(
    "/partner-performance",
    response_model=ApiResponse[PartnerPerformanceResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_partner_performance(
    filters: dict = Depends(_partner_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[PartnerPerformanceResponse]:
    """OMS SOW Module 4, Sections B & C -- full per-partner comparison
    (allocation, pickup, delivery, delivery success rate, RTO %, avg
    delivery days, avg fulfilment-to-delivery days, COD/Prepaid success
    rates). Shipping cost per delivered/RTO order (B.10/B.11) is always
    `None` -- see `shipping_cost_unavailable_reason`. The frontend's
    Decision-Making Dashboard (Section C) renders this same response as
    a sortable comparison table rather than a separate call.
    """
    data = await ShippingPartnerPerformanceService(session).get_partner_performance(**filters)
    return ApiResponse(data=PartnerPerformanceResponse(**data))


@router.get(
    "/partner-orders",
    response_model=PaginatedResponse[PerformanceOrderRowResponse],
    dependencies=[Depends(unavailable_in_live_mode_for(_TOTALS_UNAVAILABLE))],
)
async def get_partner_orders(
    metric: str = Query(default="allocated", pattern=f"^({'|'.join(PARTNER_METRICS)})$"),
    partner: str | None = Query(default=None, max_length=200),
    q: str | None = Query(default=None, max_length=100),
    filters: dict = Depends(_partner_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> PaginatedResponse[PerformanceOrderRowResponse]:
    """Orders behind a Module 4 partner row, metric cell or chart bar.
    `partner` is a row's `courier_key` (omit for all partners); `metric`
    follows the comparison table's columns.
    """
    rows, total = await PerformanceOrdersService(session).partner_orders(
        metric=metric, partner=partner, q=q, page_params=page_params, **filters
    )
    return PaginatedResponse(
        data=[PerformanceOrderRowResponse(**row) for row in rows],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


def _to_shipment_list_response(shipment: Shipment) -> ShipmentListResponse:
    order = shipment.order
    return ShipmentListResponse(
        **ShipmentResponse.model_validate(shipment).model_dump(),
        order_number=order.order_number if order else None,
        customer_name=order.customer.full_name if order and order.customer else None,
        payment_type=order.payment_type if order else None,
        confirmed_by_telecaller_name=(
            order.confirmed_by_telecaller.name if order and order.confirmed_by_telecaller else None
        ),
        courier_name=shipment.courier.name if shipment.courier else None,
    )


@router.get("", response_model=PaginatedResponse[ShipmentListResponse])
async def list_shipments(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    courier_id: uuid.UUID | None = Query(default=None),
    order_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> PaginatedResponse[ShipmentListResponse]:
    items, total = await ShipmentService(session).list_shipments(
        page_params=page_params,
        sort_params=sort_params,
        q=q,
        status=status,
        courier_id=courier_id,
        order_id=order_id,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[_to_shipment_list_response(s) for s in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("", response_model=ApiResponse[ShipmentResponse], status_code=201)
async def create_shipment(
    payload: ShipmentCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentService(session).create_shipment(
        actor=current_user, **payload.model_dump()
    )
    return ApiResponse(data=ShipmentResponse.model_validate(shipment), message="Shipment created.")


@router.post(
    "/locate-shiprocket-order",
    response_model=ApiResponse[list[LocateShiprocketOrderResult]],
)
async def locate_shiprocket_order(
    payload: LocateShiprocketOrderRequest,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[list[LocateShiprocketOrderResult]]:
    """Locate an order's EXISTING Shiprocket order id.

    "Process Shipment"/"Ship Order" (single-row) and the bulk
    Fulfillment action both call this before ever opening Shiprocket --
    resolves each order's EXISTING Shiprocket order id via
    `locate_shiprocket_orders` (never creates one; see that function's
    docstring). The frontend opens Shiprocket's plain "Ready to Ship"
    page and copies the id to the clipboard for the operator to paste
    into Shiprocket's own "Multiple Order IDs" filter -- Shiprocket has
    no supported deep-link URL for a specific order. Gated the same as
    every other Shiprocket-triggering action here (`shipments.update`)
    since a genuine match is persisted as a real `Shipment` row, not just
    read.
    """
    orders: list[Order] = []
    results: list[LocateShiprocketOrderResult] = []
    for order_id in payload.order_ids:
        order = await OrderRepository(session).get_by_id(order_id)
        if order is None:
            results.append(
                LocateShiprocketOrderResult(
                    order_id=order_id, status="error", message="Order not found."
                )
            )
        else:
            orders.append(order)

    # Captured before the call -- `locate_shiprocket_orders` may roll the
    # session back (a caught Shiprocket `IntegrationError`), which expires
    # every attribute on every attached object; a plain `.id` access after
    # would raise `MissingGreenlet` under `AsyncSession`.
    order_ids = [order.id for order in orders]
    resolved = await locate_shiprocket_orders(session, orders)
    for order_id in order_ids:
        found_id = resolved.get(order_id)
        results.append(
            LocateShiprocketOrderResult(
                order_id=order_id,
                status="found" if found_id else "not_found",
                shiprocket_order_id=found_id,
                message=(
                    None
                    if found_id
                    else "Existing Shiprocket order could not be located for this order."
                ),
            )
        )
    return ApiResponse(data=results)


@router.get("/{shipment_id}", response_model=ApiResponse[ShipmentResponse])
async def get_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentService(session).get_shipment(shipment_id)
    return ApiResponse(data=ShipmentResponse.model_validate(shipment))


@router.patch("/{shipment_id}", response_model=ApiResponse[ShipmentResponse])
async def update_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentService(session).update_shipment(
        shipment_id, actor=current_user, **payload.model_dump()
    )
    return ApiResponse(data=ShipmentResponse.model_validate(shipment), message="Shipment updated.")


@router.get("/{shipment_id}/timeline", response_model=ApiResponse[list[ShipmentEventResponse]])
async def get_shipment_timeline(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[list[ShipmentEventResponse]]:
    events = await ShipmentService(session).get_timeline(shipment_id)
    return ApiResponse(data=[ShipmentEventResponse.model_validate(e) for e in events])


@router.post("/{shipment_id}/shiprocket/assign-awb", response_model=ApiResponse[ShipmentResponse])
async def assign_awb(
    shipment_id: uuid.UUID,
    payload: ShiprocketAssignAwbRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShiprocketOperationsService(session).assign_awb(
        shipment_id, actor=current_user, courier_id=payload.courier_id
    )
    return ApiResponse(data=ShipmentResponse.model_validate(shipment), message="AWB assigned.")


@router.post("/{shipment_id}/shiprocket/cancel", response_model=ApiResponse[ShipmentResponse])
async def cancel_shipment_via_shiprocket(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShiprocketOperationsService(session).cancel_shipment(
        shipment_id, actor=current_user
    )
    return ApiResponse(
        data=ShipmentResponse.model_validate(shipment), message="Shipment cancelled."
    )


@router.post(
    "/{shipment_id}/shiprocket/request-pickup", response_model=ApiResponse[ShipmentResponse]
)
async def request_pickup(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShiprocketOperationsService(session).request_pickup(
        shipment_id, actor=current_user
    )
    return ApiResponse(data=ShipmentResponse.model_validate(shipment), message="Pickup requested.")


@router.post(
    "/{shipment_id}/shiprocket/refresh-tracking", response_model=ApiResponse[ShipmentResponse]
)
async def refresh_tracking(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShiprocketOperationsService(session).refresh_tracking_for_shipment(
        shipment_id, actor=current_user
    )
    return ApiResponse(
        data=ShipmentResponse.model_validate(shipment), message="Tracking refreshed."
    )


@router.post("/{shipment_id}/shopify/retry-sync", response_model=ApiResponse[ShipmentResponse])
async def retry_shopify_sync(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    """Manually retries the outbound Shopify fulfillment push (Part 13:
    a "Retry Shopify Sync" action, gated by `shipments.update` — ADMIN
    and FULFILLMENT have it, TELECALLER never does). Safe to call
    whenever: a no-op if already SYNCED (idempotency guard lives in
    `ShopifyFulfillmentService.sync_fulfillment_for_shipment`), and never
    raises on a Shopify-side failure — the response is always 200 with
    the shipment's current `shopify_sync_status`, which the frontend
    reads to show success/still-failed.
    """
    shipment = await ShopifyFulfillmentService(session).sync_fulfillment_for_shipment(
        shipment_id, actor=current_user
    )
    return ApiResponse(
        data=ShipmentResponse.model_validate(shipment), message="Shopify sync retried."
    )
