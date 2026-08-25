"""Order CRUD, search/filter, timeline."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError
from app.db.session import get_db
from app.dependencies.auth import get_current_user, require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.models.enums import OrderStatus
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.order import (
    OrderCreateRequest,
    OrderDetailResponse,
    OrderEventCreateRequest,
    OrderEventResponse,
    OrderResponse,
    OrderStatusTransitionRequest,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.shipment import ShipmentResponse
from app.schemas.shiprocket import ShiprocketShipRequest
from app.services.order_service import OrderService
from app.services.shiprocket_service import ShiprocketOperationsService

router = APIRouter()


@router.get("", response_model=PaginatedResponse[OrderResponse])
async def list_orders(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    payment_status: str | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("orders.read")),
) -> PaginatedResponse[OrderResponse]:
    items, total = await OrderService(session).list_orders(
        page_params=page_params,
        sort_params=sort_params,
        q=q,
        status=status,
        payment_status=payment_status,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[OrderResponse.model_validate(o) for o in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post("", response_model=ApiResponse[OrderDetailResponse], status_code=201)
async def create_order(
    payload: OrderCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("orders.create")),
) -> ApiResponse[OrderDetailResponse]:
    order = await OrderService(session).create_order(
        actor=current_user, items=payload.items, **payload.model_dump(exclude={"items"})
    )
    return ApiResponse(data=OrderDetailResponse.model_validate(order), message="Order created.")


@router.get("/{order_id}", response_model=ApiResponse[OrderDetailResponse])
async def get_order(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("orders.read")),
) -> ApiResponse[OrderDetailResponse]:
    order = await OrderService(session).get_order(order_id)
    return ApiResponse(data=OrderDetailResponse.model_validate(order))


@router.patch("/{order_id}", response_model=ApiResponse[OrderDetailResponse])
async def update_order_status(
    order_id: uuid.UUID,
    payload: OrderStatusTransitionRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[OrderDetailResponse]:
    # Cancellation is gated by its own permission (spec §9: orders.cancel is
    # distinct from orders.update) so e.g. CUSTOMER_SUPPORT can cancel
    # without also gaining the operations-only status transitions.
    required_permission = (
        "orders.cancel" if payload.status is OrderStatus.CANCELLED else "orders.update"
    )
    if not current_user.is_superuser and required_permission not in current_user.permission_codes:
        raise AuthorizationError(f"Missing required permission: {required_permission}")

    order = await OrderService(session).transition_status(
        order_id, new_status=payload.status, actor=current_user, description=payload.description
    )
    return ApiResponse(
        data=OrderDetailResponse.model_validate(order), message="Order status updated."
    )


@router.get("/{order_id}/timeline", response_model=ApiResponse[list[OrderEventResponse]])
async def get_order_timeline(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("orders.read")),
) -> ApiResponse[list[OrderEventResponse]]:
    events = await OrderService(session).get_timeline(order_id)
    return ApiResponse(data=[OrderEventResponse.model_validate(e) for e in events])


@router.post("/{order_id}/events", response_model=ApiResponse[OrderEventResponse], status_code=201)
async def add_order_event(
    order_id: uuid.UUID,
    payload: OrderEventCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("orders.update")),
) -> ApiResponse[OrderEventResponse]:
    event = await OrderService(session).add_event(
        order_id, actor=current_user, **payload.model_dump()
    )
    return ApiResponse(data=OrderEventResponse.model_validate(event), message="Event recorded.")


@router.post("/{order_id}/ship", response_model=ApiResponse[ShipmentResponse], status_code=201)
async def ship_order_via_shiprocket(
    order_id: uuid.UUID,
    payload: ShiprocketShipRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("shipments.update")),
) -> ApiResponse[ShipmentResponse]:
    """Creates a Shiprocket order + shipment from this OMS order (spec §7/§8)."""
    shipment = await ShiprocketOperationsService(session).create_shipment_for_order(
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
