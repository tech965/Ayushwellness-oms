"""Shipment CRUD, search/filter, timeline."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.models.order import Order
from app.models.shipment import Shipment
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.shipment import (
    ShipmentAnalyticsResponse,
    ShipmentCreateRequest,
    ShipmentEventResponse,
    ShipmentQueueRowResponse,
    ShipmentResponse,
    ShipmentSummaryResponse,
    ShipmentUpdateRequest,
)
from app.schemas.shiprocket import ShiprocketAssignAwbRequest
from app.services.shipment_service import ShipmentService
from app.services.shiprocket_service import ShiprocketOperationsService

router = APIRouter()


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
        confirmed_at=order.confirmed_at,
        confirmed_by_telecaller_id=order.confirmed_by_telecaller_id,
        confirmed_by_telecaller_name=(
            order.confirmed_by_telecaller.name if order.confirmed_by_telecaller else None
        ),
        shipment_id=shipment.id if shipment else None,
        shipment_status=shipment.current_status if shipment else None,
        awb=shipment.awb if shipment else None,
        courier_name=shipment.courier.name if shipment and shipment.courier else None,
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


@router.get("/summary", response_model=ApiResponse[ShipmentSummaryResponse])
async def get_shipment_summary(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[ShipmentSummaryResponse]:
    summary = await ShipmentService(session).get_summary()
    return ApiResponse(data=ShipmentSummaryResponse(**summary))


@router.get("/analytics", response_model=ApiResponse[ShipmentAnalyticsResponse])
async def get_shipment_analytics(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("shipments.read")),
) -> ApiResponse[ShipmentAnalyticsResponse]:
    analytics = await ShipmentService(session).get_analytics(date_from=date_from, date_to=date_to)
    return ApiResponse(data=ShipmentAnalyticsResponse(**analytics))


@router.get("", response_model=PaginatedResponse[ShipmentResponse])
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
) -> PaginatedResponse[ShipmentResponse]:
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
        data=[ShipmentResponse.model_validate(s) for s in items],
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
