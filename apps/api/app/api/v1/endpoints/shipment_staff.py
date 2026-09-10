"""Shipment Staff — a dedicated, SCOPED experience mirroring
`app.api.v1.endpoints.telecaller`. Every route is gated on
`shipment_staff.manage`, and every scope is hard-derived from the
authenticated user (`ShipmentStaffService.resolve_scope`) — never from a
client-supplied id — so changing an order/shipment id in the URL only
ever resolves a row actually within this Shipment Staff user's scope;
anything else is a 403 via `ShipmentStaffService.get_scoped_order`/
`get_scoped_shipment`.

The one exception is `GET /shipment-staff/admin/performance`, gated on
`shipments.read` (not `shipment_staff.manage`) — an ADMIN-facing
oversight view across every Shipment Staff user, deliberately unscoped.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.ndr import _to_list_response as _to_ndr_list_response
from app.api.v1.endpoints.rto import _to_list_response as _to_rto_list_response
from app.api.v1.endpoints.shipments import _to_shipment_queue_row
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.models.order import Order
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.ndr import NDRListResponse
from app.schemas.order import OrderDetailResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.rto import RTOListResponse
from app.schemas.shipment import (
    ShipmentAnalyticsResponse,
    ShipmentEventResponse,
    ShipmentQueueRowResponse,
    ShipmentResponse,
    ShipmentSummaryResponse,
)
from app.schemas.shipment_staff import ShipmentStaffPerformanceResponse
from app.schemas.shiprocket import (
    LocateShiprocketOrderRequest,
    LocateShiprocketOrderResult,
    ShiprocketAssignAwbRequest,
    ShiprocketNdrReattemptRequest,
    ShiprocketShipRequest,
)
from app.services.shipment_staff_service import ShipmentStaffService
from app.services.shiprocket_service import locate_shiprocket_orders

router = APIRouter()


@router.get("/orders", response_model=PaginatedResponse[ShipmentQueueRowResponse])
async def list_my_confirmed_orders(
    q: str | None = Query(default=None),
    payment_type: str | None = Query(default=None),
    courier_id: uuid.UUID | None = Query(default=None),
    sku: str | None = Query(default=None),
    shipment_status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> PaginatedResponse[ShipmentQueueRowResponse]:
    """Confirmed orders awaiting shipment — restricted to Telecallers
    whose `shipment_staff_id` is this caller (see
    `ShipmentStaffService.resolve_scope`), never every confirmed order.
    """
    rows, total = await ShipmentStaffService(session).list_confirmed_orders(
        actor=current_user,
        page_params=page_params,
        q=q,
        payment_type=payment_type,
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


@router.post(
    "/orders/locate-shiprocket-order",
    response_model=ApiResponse[list[LocateShiprocketOrderResult]],
)
async def locate_shiprocket_order_for_my_scope(
    payload: LocateShiprocketOrderRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[list[LocateShiprocketOrderResult]]:
    """Scoped equivalent of `POST /shipments/locate-shiprocket-order` —
    every id is re-resolved through `ShipmentStaffService.get_scoped_order`
    (never trusted from the client), so this can only ever locate a
    Shiprocket order for one of this Shipment Staff user's own scoped
    orders. See `locate_shiprocket_orders` for the actual (never-creates)
    resolution logic, shared unchanged with the Fulfillment/Admin queue.
    """
    service = ShipmentStaffService(session)
    orders: list[Order] = []
    results: list[LocateShiprocketOrderResult] = []
    for order_id in payload.order_ids:
        try:
            orders.append(await service.get_scoped_order(order_id, actor=current_user))
        except (NotFoundError, AuthorizationError):
            results.append(
                LocateShiprocketOrderResult(
                    order_id=order_id,
                    status="error",
                    message="Order not found or not in your scope.",
                )
            )

    # Captured before the call -- see the comment in the equivalent
    # `POST /shipments/locate-shiprocket-order` endpoint.
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


@router.get("/orders/{order_id}", response_model=ApiResponse[OrderDetailResponse])
async def get_my_confirmed_order(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[OrderDetailResponse]:
    order = await ShipmentStaffService(session).get_scoped_order(order_id, actor=current_user)
    return ApiResponse(data=OrderDetailResponse.model_validate(order))


@router.post(
    "/orders/{order_id}/ship", response_model=ApiResponse[ShipmentResponse], status_code=201
)
async def ship_my_confirmed_order(
    order_id: uuid.UUID,
    payload: ShiprocketShipRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentStaffService(session).create_shipment(
        order_id,
        actor=current_user,
        length_cm=payload.length_cm,
        breadth_cm=payload.breadth_cm,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
    )
    return ApiResponse(
        data=ShipmentResponse.model_validate(shipment), message="Shipment created via Shiprocket."
    )


@router.get("/shipments", response_model=PaginatedResponse[ShipmentResponse])
async def list_my_shipments(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    courier_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> PaginatedResponse[ShipmentResponse]:
    items, total = await ShipmentStaffService(session).list_shipments(
        actor=current_user,
        page_params=page_params,
        sort_params=sort_params,
        q=q,
        status=status,
        courier_id=courier_id,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[ShipmentResponse.model_validate(s) for s in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/shipments/{shipment_id}", response_model=ApiResponse[ShipmentResponse])
async def get_my_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentStaffService(session).get_scoped_shipment(
        shipment_id, actor=current_user
    )
    return ApiResponse(data=ShipmentResponse.model_validate(shipment))


@router.get(
    "/shipments/{shipment_id}/timeline", response_model=ApiResponse[list[ShipmentEventResponse]]
)
async def get_my_shipment_timeline(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[list[ShipmentEventResponse]]:
    events = await ShipmentStaffService(session).get_timeline(shipment_id, actor=current_user)
    return ApiResponse(data=[ShipmentEventResponse.model_validate(e) for e in events])


@router.post("/shipments/{shipment_id}/assign-awb", response_model=ApiResponse[ShipmentResponse])
async def assign_awb_for_my_shipment(
    shipment_id: uuid.UUID,
    payload: ShiprocketAssignAwbRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentStaffService(session).assign_awb(
        shipment_id, actor=current_user, courier_id=payload.courier_id
    )
    return ApiResponse(data=ShipmentResponse.model_validate(shipment), message="AWB assigned.")


@router.post(
    "/shipments/{shipment_id}/request-pickup", response_model=ApiResponse[ShipmentResponse]
)
async def request_pickup_for_my_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentStaffService(session).request_pickup(shipment_id, actor=current_user)
    return ApiResponse(data=ShipmentResponse.model_validate(shipment), message="Pickup requested.")


@router.post(
    "/shipments/{shipment_id}/refresh-tracking", response_model=ApiResponse[ShipmentResponse]
)
async def refresh_tracking_for_my_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentStaffService(session).refresh_tracking(
        shipment_id, actor=current_user
    )
    return ApiResponse(
        data=ShipmentResponse.model_validate(shipment), message="Tracking refreshed."
    )


@router.post(
    "/shipments/{shipment_id}/shopify/retry-sync", response_model=ApiResponse[ShipmentResponse]
)
async def retry_shopify_sync_for_my_shipment(
    shipment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentResponse]:
    shipment = await ShipmentStaffService(session).retry_shopify_sync(
        shipment_id, actor=current_user
    )
    return ApiResponse(
        data=ShipmentResponse.model_validate(shipment), message="Shopify sync retried."
    )


@router.get("/summary", response_model=ApiResponse[ShipmentSummaryResponse])
async def get_my_summary(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentSummaryResponse]:
    summary = await ShipmentStaffService(session).get_summary(actor=current_user)
    return ApiResponse(data=ShipmentSummaryResponse(**summary))


@router.get("/analytics", response_model=ApiResponse[ShipmentAnalyticsResponse])
async def get_my_analytics(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[ShipmentAnalyticsResponse]:
    analytics = await ShipmentStaffService(session).get_analytics(
        actor=current_user, date_from=date_from, date_to=date_to
    )
    return ApiResponse(data=ShipmentAnalyticsResponse(**analytics))


@router.get("/ndr", response_model=PaginatedResponse[NDRListResponse])
async def list_my_ndr(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> PaginatedResponse[NDRListResponse]:
    items, total = await ShipmentStaffService(session).list_ndr(
        actor=current_user,
        page_params=page_params,
        sort_params=sort_params,
        q=q,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[_to_ndr_list_response(n) for n in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("/ndr/{ndr_id}/reattempt", response_model=ApiResponse[NDRListResponse])
async def request_ndr_reattempt_for_my_scope(
    ndr_id: uuid.UUID,
    payload: ShiprocketNdrReattemptRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> ApiResponse[NDRListResponse]:
    ndr = await ShipmentStaffService(session).reattempt_ndr(
        ndr_id,
        actor=current_user,
        address_1=payload.address_1,
        address_2=payload.address_2,
        phone=payload.phone,
    )
    return ApiResponse(data=_to_ndr_list_response(ndr), message="Reattempt requested.")


@router.get("/rto", response_model=PaginatedResponse[RTOListResponse])
async def list_my_rto(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipment_staff.manage")),
) -> PaginatedResponse[RTOListResponse]:
    items, total = await ShipmentStaffService(session).list_rto(
        actor=current_user,
        page_params=page_params,
        sort_params=sort_params,
        q=q,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[_to_rto_list_response(r) for r in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get(
    "/admin/performance", response_model=ApiResponse[list[ShipmentStaffPerformanceResponse]]
)
async def get_shipment_staff_performance(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[list[ShipmentStaffPerformanceResponse]]:
    """ADMIN/OPERATIONS/MANAGEMENT oversight (Part 7) — every Shipment
    Staff user, their assigned-Telecaller count, and aggregated
    confirmed/shipped/delivered/NDR/RTO figures across that scope.
    Deliberately unscoped and gated on `shipments.read`, not
    `shipment_staff.manage` — this is an admin view OF Shipment Staff,
    never reachable from within a Shipment Staff user's own scope.
    """
    results = await ShipmentStaffService(session).list_shipment_staff_performance()
    return ApiResponse(data=[ShipmentStaffPerformanceResponse(**r) for r in results])
