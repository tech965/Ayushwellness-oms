"""Team Leader (and Admin) order-assignment and reporting endpoints.

Every route is gated on `telecalling.manage`. `/orders/*` is completely
untouched by this module — these are new, additive routes that reuse
`OrderRepository`/`OrderAssignmentRepository` internals rather than
overloading the existing Admin Orders endpoints with role-conditional
scoping.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.abandoned_checkout import AbandonedCheckout
from app.models.auth import User
from app.models.enums import LeadCategory
from app.models.order import Order
from app.models.telecalling import CheckoutAssignment, OrderAssignment
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.schemas.telecalling import (
    AssignCheckoutsRequest,
    AssignedCheckoutResponse,
    AssignedOrderResponse,
    AssignOrdersRequest,
    CallAttemptResponse,
    CheckoutAssignmentResponse,
    CheckoutCallAttemptResponse,
    OrderAssignmentResponse,
    ReassignCheckoutRequest,
    ReassignOrderRequest,
    TelecallerPerformanceResponse,
    TelecallingSummaryResponse,
)
from app.services.lead_classification import classify_order, classify_priority
from app.services.telecalling_service import ScopeFilter, TelecallingService, resolve_team_scope

router = APIRouter()


def to_assigned_order_response(
    order: Order, assignment: OrderAssignment | None = None, *, telecaller_name: str | None = None
) -> AssignedOrderResponse:
    """Flattens an `Order` + its (possibly absent) active `OrderAssignment`
    into one row. `assignment=None` only ever happens in the Team Leader's
    unfulfilled-orders *pool* view — every other caller passes a real
    assignment (`assignment.order` for the assignment-driven list/detail
    endpoints).
    """
    items = order.items
    item_summary = None
    if items:
        first = items[0].product_name
        item_summary = first if len(items) == 1 else f"{first} +{len(items) - 1} more"

    category = classify_order(
        payment_type=order.payment_type, fulfillment_status=order.fulfillment_status
    )
    priority = (
        classify_priority(
            category=category,
            next_follow_up_at=assignment.next_follow_up_at if assignment else None,
            reference_datetime=order.order_datetime,
        )
        if category is not None
        else None
    )

    return AssignedOrderResponse(
        order_id=order.id,
        order_number=order.order_number,
        customer_name=order.customer.full_name if order.customer else None,
        customer_phone=order.customer.phone if order.customer else None,
        item_summary=item_summary,
        total_amount=order.total_amount,
        payment_type=order.payment_type,
        payment_status=order.payment_status,
        fulfillment_status=order.fulfillment_status,
        order_datetime=order.order_datetime,
        shipping_address=order.shipping_address,
        assignment_id=assignment.id if assignment else None,
        assigned_to=assignment.assigned_to if assignment else None,
        assigned_to_name=telecaller_name,
        call_status=assignment.current_status if assignment else None,
        attempt_count=assignment.attempt_count if assignment else 0,
        last_attempt_at=assignment.last_attempt_at if assignment else None,
        next_follow_up_at=assignment.next_follow_up_at if assignment else None,
        lead_category=category,
        priority=priority,
    )


def to_assigned_checkout_response(
    checkout: AbandonedCheckout,
    assignment: CheckoutAssignment | None = None,
    *,
    telecaller_name: str | None = None,
) -> AssignedCheckoutResponse:
    """`to_assigned_order_response`'s exact counterpart for an abandoned
    checkout lead."""
    items = checkout.line_items or []
    item_summary = None
    if items:
        first = items[0].get("title") or "Item"
        item_summary = first if len(items) == 1 else f"{first} +{len(items) - 1} more"

    reference_datetime = checkout.checkout_created_at or checkout.created_at
    priority = classify_priority(
        category=LeadCategory.ABANDONED_CHECKOUT,
        next_follow_up_at=assignment.next_follow_up_at if assignment else None,
        reference_datetime=reference_datetime,
    )

    customer = checkout.customer
    return AssignedCheckoutResponse(
        checkout_id=checkout.id,
        customer_name=checkout.customer_name or (customer.full_name if customer else None),
        customer_phone=checkout.customer_phone or (customer.phone if customer else None),
        customer_email=checkout.customer_email or (customer.email if customer else None),
        item_summary=item_summary,
        total_amount=checkout.total_amount,
        checkout_url=checkout.checkout_url,
        checkout_created_at=checkout.checkout_created_at,
        is_recovered=checkout.is_recovered,
        assignment_id=assignment.id if assignment else None,
        assigned_to=assignment.assigned_to if assignment else None,
        assigned_to_name=telecaller_name,
        call_status=assignment.current_status if assignment else None,
        attempt_count=assignment.attempt_count if assignment else 0,
        last_attempt_at=assignment.last_attempt_at if assignment else None,
        next_follow_up_at=assignment.next_follow_up_at if assignment else None,
        priority=priority,
    )


@router.get("/orders/unfulfilled", response_model=PaginatedResponse[AssignedOrderResponse])
async def list_unfulfilled_team_orders(
    call_status: str | None = Query(default=None),
    telecaller_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> PaginatedResponse[AssignedOrderResponse]:
    service = TelecallingService(session)
    scope = resolve_team_scope(current_user)

    if telecaller_id is not None:
        # Narrowed to one telecaller's own (already-assigned) unfulfilled
        # orders — a team leader may only narrow to one of *their own*
        # telecallers, never widen to someone else's.
        await service.assert_telecaller_in_team_scope(telecaller_id, actor=current_user)
        narrowed = ScopeFilter(assigned_to=telecaller_id, team_leader_id=None)
        items, total = await service.list_assignments(
            scope=narrowed,
            page_params=page_params,
            sort_params=sort_params,
            unfulfilled_only=True,
            call_status=call_status,
            date_from=date_from,
            date_to=date_to,
        )
        data = [to_assigned_order_response(a.order, a) for a in items]
    else:
        # The default browsing surface: every unfulfilled order that's
        # either unassigned (available to grab) or already on the
        # caller's own team — see `list_unfulfilled_pool`'s docstring.
        pairs, total = await service.list_unfulfilled_pool(
            scope=scope,
            page_params=page_params,
            call_status=call_status,
            date_from=date_from,
            date_to=date_to,
        )
        data = [to_assigned_order_response(order, assignment) for order, assignment in pairs]

    return PaginatedResponse(
        data=data, meta=build_pagination_meta(total_items=total, page_params=page_params)
    )


@router.get("/leads", response_model=PaginatedResponse[AssignedOrderResponse])
async def list_lead_pool(
    category: LeadCategory | None = Query(default=None),
    call_status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> PaginatedResponse[AssignedOrderResponse]:
    """The widened Admin/Manager Lead Pool — COD Unfulfilled / COD
    Fulfilled / Prepaid orders (spec: "COD Fulfilled and Prepaid orders
    become assignable through that same pool, filtered by category"),
    optionally narrowed to one `category`. Leaves `/orders/unfulfilled`
    above completely untouched.
    """
    pairs, total = await TelecallingService(session).list_lead_pool(
        scope=resolve_team_scope(current_user),
        category=category,
        page_params=page_params,
        call_status=call_status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[to_assigned_order_response(order, assignment) for order, assignment in pairs],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/orders/{order_id}", response_model=ApiResponse[AssignedOrderResponse])
async def get_team_order(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[AssignedOrderResponse]:
    service = TelecallingService(session)
    assignment = await service.get_scoped_assignment(
        order_id, scope=resolve_team_scope(current_user)
    )
    return ApiResponse(data=to_assigned_order_response(assignment.order, assignment))


@router.get("/orders/{order_id}/calls", response_model=ApiResponse[list[CallAttemptResponse]])
async def get_team_order_call_history(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[list[CallAttemptResponse]]:
    """Read-only for the Team Leader — reviewing a telecaller's call
    history on one of their team's orders, scoped the same as
    `get_team_order`.
    """
    service = TelecallingService(session)
    attempts = await service.list_call_history(order_id, scope=resolve_team_scope(current_user))
    return ApiResponse(data=[CallAttemptResponse.model_validate(a) for a in attempts])


@router.post(
    "/orders/assign", response_model=ApiResponse[list[OrderAssignmentResponse]], status_code=201
)
async def assign_orders(
    payload: AssignOrdersRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[list[OrderAssignmentResponse]]:
    assignments = await TelecallingService(session).assign_orders(
        order_ids=payload.order_ids,
        mode=payload.mode,
        telecaller_id=payload.telecaller_id,
        telecaller_ids=payload.telecaller_ids,
        actor=current_user,
    )
    return ApiResponse(
        data=[OrderAssignmentResponse.model_validate(a) for a in assignments],
        message=f"{len(assignments)} order(s) assigned.",
    )


@router.post(
    "/orders/reassign", response_model=ApiResponse[OrderAssignmentResponse], status_code=201
)
async def reassign_order(
    payload: ReassignOrderRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[OrderAssignmentResponse]:
    assignment = await TelecallingService(session).reassign_order(
        order_id=payload.order_id,
        new_telecaller_id=payload.new_telecaller_id,
        reason=payload.reason,
        actor=current_user,
    )
    return ApiResponse(
        data=OrderAssignmentResponse.model_validate(assignment), message="Order reassigned."
    )


@router.get("/checkouts", response_model=PaginatedResponse[AssignedCheckoutResponse])
async def list_team_checkouts(
    call_status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> PaginatedResponse[AssignedCheckoutResponse]:
    """The Admin/Manager Abandoned Checkout pool — same
    unassigned-or-own-team browsing surface as `/orders/unfulfilled`, for
    contactable open checkouts (see
    `CheckoutAssignmentRepository.list_pool`'s docstring)."""
    pairs, total = await TelecallingService(session).list_checkout_pool(
        scope=resolve_team_scope(current_user),
        page_params=page_params,
        call_status=call_status,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedResponse(
        data=[
            to_assigned_checkout_response(checkout, assignment) for checkout, assignment in pairs
        ],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/checkouts/{checkout_id}", response_model=ApiResponse[AssignedCheckoutResponse])
async def get_team_checkout(
    checkout_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[AssignedCheckoutResponse]:
    service = TelecallingService(session)
    assignment = await service.get_scoped_checkout_assignment(
        checkout_id, scope=resolve_team_scope(current_user)
    )
    return ApiResponse(data=to_assigned_checkout_response(assignment.checkout, assignment))


@router.get(
    "/checkouts/{checkout_id}/calls", response_model=ApiResponse[list[CheckoutCallAttemptResponse]]
)
async def get_team_checkout_call_history(
    checkout_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[list[CheckoutCallAttemptResponse]]:
    service = TelecallingService(session)
    attempts = await service.list_checkout_call_history(
        checkout_id, scope=resolve_team_scope(current_user)
    )
    return ApiResponse(data=[CheckoutCallAttemptResponse.model_validate(a) for a in attempts])


@router.post(
    "/checkouts/assign",
    response_model=ApiResponse[list[CheckoutAssignmentResponse]],
    status_code=201,
)
async def assign_checkouts(
    payload: AssignCheckoutsRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[list[CheckoutAssignmentResponse]]:
    assignments = await TelecallingService(session).assign_checkouts(
        checkout_ids=payload.checkout_ids,
        mode=payload.mode,
        telecaller_id=payload.telecaller_id,
        telecaller_ids=payload.telecaller_ids,
        actor=current_user,
    )
    return ApiResponse(
        data=[CheckoutAssignmentResponse.model_validate(a) for a in assignments],
        message=f"{len(assignments)} checkout(s) assigned.",
    )


@router.post(
    "/checkouts/reassign", response_model=ApiResponse[CheckoutAssignmentResponse], status_code=201
)
async def reassign_checkout(
    payload: ReassignCheckoutRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[CheckoutAssignmentResponse]:
    assignment = await TelecallingService(session).reassign_checkout(
        checkout_id=payload.checkout_id,
        new_telecaller_id=payload.new_telecaller_id,
        reason=payload.reason,
        actor=current_user,
    )
    return ApiResponse(
        data=CheckoutAssignmentResponse.model_validate(assignment), message="Checkout reassigned."
    )


@router.get("/telecallers", response_model=ApiResponse[list[TelecallerPerformanceResponse]])
async def list_team_telecallers(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[list[TelecallerPerformanceResponse]]:
    performance = await TelecallingService(session).team_telecaller_performance(
        scope=resolve_team_scope(current_user)
    )
    return ApiResponse(data=[TelecallerPerformanceResponse(**p) for p in performance])


@router.get(
    "/telecallers/{telecaller_id}/orders", response_model=PaginatedResponse[AssignedOrderResponse]
)
async def get_telecaller_orders(
    telecaller_id: uuid.UUID,
    call_status: str | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> PaginatedResponse[AssignedOrderResponse]:
    service = TelecallingService(session)
    await service.assert_telecaller_in_team_scope(telecaller_id, actor=current_user)
    items, total = await service.list_assignments(
        scope=ScopeFilter(assigned_to=telecaller_id, team_leader_id=None),
        page_params=page_params,
        sort_params=sort_params,
        call_status=call_status,
    )
    return PaginatedResponse(
        data=[to_assigned_order_response(a.order, a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get(
    "/telecallers/{telecaller_id}/checkouts",
    response_model=PaginatedResponse[AssignedCheckoutResponse],
)
async def get_telecaller_checkouts(
    telecaller_id: uuid.UUID,
    call_status: str | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> PaginatedResponse[AssignedCheckoutResponse]:
    service = TelecallingService(session)
    await service.assert_telecaller_in_team_scope(telecaller_id, actor=current_user)
    items, total = await service.list_checkout_assignments(
        scope=ScopeFilter(assigned_to=telecaller_id, team_leader_id=None),
        page_params=page_params,
        sort_params=sort_params,
        call_status=call_status,
    )
    return PaginatedResponse(
        data=[to_assigned_checkout_response(a.checkout, a) for a in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/summary", response_model=ApiResponse[TelecallingSummaryResponse])
async def get_team_summary(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("telecalling.manage")),
) -> ApiResponse[TelecallingSummaryResponse]:
    summary = await TelecallingService(session).team_summary(scope=resolve_team_scope(current_user))
    return ApiResponse(data=TelecallingSummaryResponse(**summary))
