"""Telecaller's own-orders calling workflow.

Every route is gated on `calls.manage`, and every scope is hard-derived
from the authenticated user (`resolve_telecaller_scope`) — never from a
client-supplied id — so changing the order id in the URL only ever
resolves an order actually assigned to the caller; anything else is a 403
via `TelecallingService.get_scoped_assignment`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.team import to_assigned_checkout_response, to_assigned_order_response
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.telecalling import (
    AssignedCheckoutResponse,
    AssignedOrderResponse,
    CallAttemptResponse,
    CallHistoryEntryResponse,
    CheckoutCallAttemptResponse,
    LogCallRequest,
    OrderAssignmentResponse,
    ScheduleFollowUpRequest,
    TelecallingSummaryResponse,
)
from app.services.telecalling_service import TelecallingService, resolve_telecaller_scope

router = APIRouter()


@router.get("/orders", response_model=PaginatedResponse[AssignedOrderResponse])
async def list_my_orders(
    call_status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> PaginatedResponse[AssignedOrderResponse]:
    items, total = await TelecallingService(session).list_assignments(
        scope=resolve_telecaller_scope(current_user),
        page_params=page_params,
        sort_params=sort_params,
        call_status=call_status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[to_assigned_order_response(a.order, a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/orders/{order_id}", response_model=ApiResponse[AssignedOrderResponse])
async def get_my_order(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[AssignedOrderResponse]:
    service = TelecallingService(session)
    assignment = await service.get_scoped_assignment(
        order_id, scope=resolve_telecaller_scope(current_user)
    )
    return ApiResponse(data=to_assigned_order_response(assignment.order, assignment))


@router.get("/orders/{order_id}/calls", response_model=ApiResponse[list[CallAttemptResponse]])
async def get_call_history(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[list[CallAttemptResponse]]:
    service = TelecallingService(session)
    attempts = await service.list_call_history(
        order_id, scope=resolve_telecaller_scope(current_user)
    )
    return ApiResponse(data=[CallAttemptResponse.model_validate(a) for a in attempts])


@router.post(
    "/orders/{order_id}/calls", response_model=ApiResponse[CallAttemptResponse], status_code=201
)
async def log_call(
    order_id: uuid.UUID,
    payload: LogCallRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[CallAttemptResponse]:
    attempt = await TelecallingService(session).log_call(
        order_id,
        outcome=payload.outcome,
        notes=payload.notes,
        next_follow_up_at=payload.next_follow_up_at,
        actor=current_user,
    )
    return ApiResponse(data=CallAttemptResponse.model_validate(attempt), message="Call logged.")


@router.post("/orders/{order_id}/follow-up", response_model=ApiResponse[OrderAssignmentResponse])
async def schedule_follow_up(
    order_id: uuid.UUID,
    payload: ScheduleFollowUpRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[OrderAssignmentResponse]:
    assignment = await TelecallingService(session).schedule_follow_up(
        order_id, next_follow_up_at=payload.next_follow_up_at, actor=current_user
    )
    return ApiResponse(
        data=OrderAssignmentResponse.model_validate(assignment), message="Follow-up scheduled."
    )


@router.get("/follow-ups", response_model=PaginatedResponse[AssignedOrderResponse])
async def list_my_follow_ups(
    when: str = Query(default="today", pattern="^(today|overdue|upcoming)$"),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> PaginatedResponse[AssignedOrderResponse]:
    items, total = await TelecallingService(session).list_assignments(
        scope=resolve_telecaller_scope(current_user),
        page_params=page_params,
        sort_params=sort_params,
        when=when,
    )
    return PaginatedResponse(
        data=[to_assigned_order_response(a.order, a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/checkouts", response_model=PaginatedResponse[AssignedCheckoutResponse])
async def list_my_checkouts(
    call_status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> PaginatedResponse[AssignedCheckoutResponse]:
    items, total = await TelecallingService(session).list_checkout_assignments(
        scope=resolve_telecaller_scope(current_user),
        page_params=page_params,
        sort_params=sort_params,
        call_status=call_status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[to_assigned_checkout_response(a.checkout, a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/checkouts/{checkout_id}", response_model=ApiResponse[AssignedCheckoutResponse])
async def get_my_checkout(
    checkout_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[AssignedCheckoutResponse]:
    service = TelecallingService(session)
    assignment = await service.get_scoped_checkout_assignment(
        checkout_id, scope=resolve_telecaller_scope(current_user)
    )
    return ApiResponse(data=to_assigned_checkout_response(assignment.checkout, assignment))


@router.get(
    "/checkouts/{checkout_id}/calls", response_model=ApiResponse[list[CheckoutCallAttemptResponse]]
)
async def get_checkout_call_history(
    checkout_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[list[CheckoutCallAttemptResponse]]:
    service = TelecallingService(session)
    attempts = await service.list_checkout_call_history(
        checkout_id, scope=resolve_telecaller_scope(current_user)
    )
    return ApiResponse(data=[CheckoutCallAttemptResponse.model_validate(a) for a in attempts])


@router.post(
    "/checkouts/{checkout_id}/calls",
    response_model=ApiResponse[CheckoutCallAttemptResponse],
    status_code=201,
)
async def log_checkout_call(
    checkout_id: uuid.UUID,
    payload: LogCallRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[CheckoutCallAttemptResponse]:
    attempt = await TelecallingService(session).log_checkout_call(
        checkout_id,
        outcome=payload.outcome,
        notes=payload.notes,
        next_follow_up_at=payload.next_follow_up_at,
        actor=current_user,
    )
    return ApiResponse(
        data=CheckoutCallAttemptResponse.model_validate(attempt), message="Call logged."
    )


@router.post(
    "/checkouts/{checkout_id}/follow-up", response_model=ApiResponse[AssignedCheckoutResponse]
)
async def schedule_checkout_follow_up(
    checkout_id: uuid.UUID,
    payload: ScheduleFollowUpRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[AssignedCheckoutResponse]:
    assignment = await TelecallingService(session).schedule_checkout_follow_up(
        checkout_id, next_follow_up_at=payload.next_follow_up_at, actor=current_user
    )
    return ApiResponse(
        data=to_assigned_checkout_response(assignment.checkout, assignment),
        message="Follow-up scheduled.",
    )


@router.get("/checkout-follow-ups", response_model=PaginatedResponse[AssignedCheckoutResponse])
async def list_my_checkout_follow_ups(
    when: str = Query(default="today", pattern="^(today|overdue|upcoming)$"),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> PaginatedResponse[AssignedCheckoutResponse]:
    items, total = await TelecallingService(session).list_checkout_assignments(
        scope=resolve_telecaller_scope(current_user),
        page_params=page_params,
        sort_params=sort_params,
        when=when,
    )
    return PaginatedResponse(
        data=[to_assigned_checkout_response(a.checkout, a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/summary", response_model=ApiResponse[TelecallingSummaryResponse])
async def get_my_summary(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[TelecallingSummaryResponse]:
    summary = await TelecallingService(session).telecaller_summary(
        scope=resolve_telecaller_scope(current_user)
    )
    return ApiResponse(data=TelecallingSummaryResponse(**summary))


@router.get("/calls", response_model=ApiResponse[list[CallHistoryEntryResponse]])
async def get_my_call_history(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("calls.manage")),
) -> ApiResponse[list[CallHistoryEntryResponse]]:
    """Every call the caller has personally made, across every order —
    always their own id, never a request parameter.
    """
    entries = await TelecallingService(session).list_my_call_history(current_user.id)
    return ApiResponse(
        data=[
            CallHistoryEntryResponse(
                **CallAttemptResponse.model_validate(attempt).model_dump(),
                order_number=order_number,
            )
            for attempt, order_number in entries
        ]
    )
