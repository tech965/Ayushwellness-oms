from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    AddressValidationStatus,
    FulfillmentStatus,
    LeadCategory,
    LeadPriority,
    OrderStatus,
    PaymentStatus,
    PaymentType,
    TelecallingStatus,
)


class OrderAddressUpdateRequest(BaseModel):
    """Editable shipping-address fields — exactly `Order.shipping_
    address`'s existing dict shape (see `app.integrations.shopify.
    normalizer.normalize_address`), never a new/duplicate address
    concept. Field widths match that same normalizer's `max_len` values.
    """

    contact_name: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=32)
    line1: str = Field(min_length=1, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=1, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    pin_code: str = Field(min_length=1, max_length=16)
    country: str = Field(min_length=1, max_length=120, default="India")


class AssignOrdersRequest(BaseModel):
    order_ids: list[uuid.UUID] = Field(min_length=1)
    mode: Literal["manual", "equal"]
    telecaller_id: uuid.UUID | None = None
    telecaller_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def _check_mode_fields(self) -> AssignOrdersRequest:
        if self.mode == "manual" and not self.telecaller_id:
            raise ValueError("telecaller_id is required for manual assignment.")
        if self.mode == "equal" and not self.telecaller_ids:
            raise ValueError("telecaller_ids is required for equal-distribution assignment.")
        return self


class ReassignOrderRequest(BaseModel):
    order_id: uuid.UUID
    new_telecaller_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


class AssignCheckoutsRequest(BaseModel):
    checkout_ids: list[uuid.UUID] = Field(min_length=1)
    mode: Literal["manual", "equal"]
    telecaller_id: uuid.UUID | None = None
    telecaller_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def _check_mode_fields(self) -> AssignCheckoutsRequest:
        if self.mode == "manual" and not self.telecaller_id:
            raise ValueError("telecaller_id is required for manual assignment.")
        if self.mode == "equal" and not self.telecaller_ids:
            raise ValueError("telecaller_ids is required for equal-distribution assignment.")
        return self


class ReassignCheckoutRequest(BaseModel):
    checkout_id: uuid.UUID
    new_telecaller_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


class LogCallRequest(BaseModel):
    outcome: TelecallingStatus
    notes: str | None = Field(default=None, max_length=4000)
    next_follow_up_at: datetime | None = None

    @model_validator(mode="after")
    def _reject_not_called(self) -> LogCallRequest:
        if self.outcome == TelecallingStatus.NOT_CALLED:
            raise ValueError("NOT_CALLED is not a loggable call outcome.")
        return self


class ScheduleFollowUpRequest(BaseModel):
    next_follow_up_at: datetime


class BulkConfirmOrdersRequest(BaseModel):
    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class BulkConfirmOrderResult(BaseModel):
    order_id: uuid.UUID
    success: bool
    message: str | None = None


class BulkConfirmOrdersResponse(BaseModel):
    confirmed_count: int
    failed_count: int
    results: list[BulkConfirmOrderResult]


class CallAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    telecaller_id: uuid.UUID | None
    attempt_number: int
    attempted_at: datetime
    outcome: TelecallingStatus
    notes: str | None
    next_follow_up_at: datetime | None
    created_at: datetime
    is_edited: bool = False


class OrderAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    assigned_to: uuid.UUID
    assigned_by: uuid.UUID | None
    assigned_at: datetime
    team_leader_id: uuid.UUID | None
    assignment_status: str
    reassigned_from: uuid.UUID | None
    reassigned_to: uuid.UUID | None
    reassigned_at: datetime | None
    reassignment_reason: str | None
    current_status: TelecallingStatus
    attempt_count: int
    last_attempt_at: datetime | None
    next_follow_up_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AssignedOrderItemResponse(BaseModel):
    """One order line item, enough to show "[image] product name /
    variant title, SKU: ..." on the Telecaller order detail page — see
    `app.api.v1.endpoints.team.to_assigned_order_response`. `sku`/
    `product_name`/quantity/price fields are `OrderItem` columns
    (verbatim, never invented); `variant_title`/`image_url` come from the
    related `ProductVariant`/`Product` (via `OrderItem.product_variant`)
    and are `None` whenever that relationship never resolved (unsynced
    SKU, manual order, or — for `image_url` — a product with no Shopify
    image) rather than a data gap.
    """

    id: uuid.UUID
    sku: str
    product_name: str
    variant_title: str | None = None
    image_url: str | None = None
    quantity: int
    unit_price: Decimal
    total_amount: Decimal


class AssignedOrderResponse(BaseModel):
    """One row of a Team Leader's/Telecaller's order list — flattens the
    order + its active assignment into the exact columns the spec's
    tables ask for, the same "denormalize once, no N+1" convention as
    `OrderListResponse` (`app/schemas/order.py`).
    """

    order_id: uuid.UUID
    order_number: str
    customer_name: str | None
    customer_phone: str | None
    item_summary: str | None
    total_amount: Decimal
    payment_type: PaymentType
    payment_status: PaymentStatus
    # `Order.status` — the order-confirmation/pack-ship workflow. Never
    # the same fact as `call_status` below (`TelecallingStatus`, a call
    # outcome) — the frontend must not conflate the two.
    status: OrderStatus
    fulfillment_status: FulfillmentStatus
    confirmed_at: datetime | None = None
    confirmed_by_telecaller_id: uuid.UUID | None = None
    order_datetime: datetime
    shipping_address: dict | None = None
    # Outbound OMS -> Shopify sync state for `shipping_address` above
    # (`ShopifyFulfillmentService.sync_shipping_address`) -- lets the
    # frontend tell "saved and synced to Shopify" apart from "saved in
    # OMS, Shopify sync failed" after an address edit, rather than
    # reporting blanket success either way.
    shipping_address_sync_status: str | None = None
    shipping_address_sync_error: str | None = None
    # Same already-loaded `Order` row as everything else on this
    # response -- zero extra queries. `None` means "not yet validated",
    # shown by the frontend as "Validation pending", never guessed.
    shipping_address_validation_status: AddressValidationStatus | None = None
    shipping_address_validation_score: int | None = None
    # Line items for the Product section (image + name + variant + SKU) —
    # see `AssignedOrderItemResponse`.
    items: list[AssignedOrderItemResponse] = []
    # Null across this whole block means "not yet assigned to anyone" —
    # only possible in the Team Leader's unfulfilled-orders *pool* view
    # (`GET /team/orders/unfulfilled` with no `telecaller_id` filter),
    # which deliberately surfaces the unassigned backlog alongside
    # already-assigned-within-team orders so there's something to select
    # and assign in the first place. Every other list (a telecaller's own
    # orders, one telecaller's workload) only ever returns assigned rows.
    assignment_id: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None
    assigned_to_name: str | None = None
    call_status: TelecallingStatus | None = None
    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    # `None` only for `PaymentType.OTHER` — not one of the spec's defined
    # categories (see `app.services.lead_classification.classify_order`).
    lead_category: LeadCategory | None = None
    priority: LeadPriority | None = None


class AssignedCheckoutResponse(BaseModel):
    """`AssignedOrderResponse`'s counterpart for an abandoned-checkout
    lead — same "denormalize once" shape, flattening an `AbandonedCheckout`
    + its (possibly absent) active `CheckoutAssignment` into one row.
    `lead_category` is always `ABANDONED_CHECKOUT` here (included anyway
    so both lead types share one column set in the frontend's unified
    lead table).
    """

    checkout_id: uuid.UUID
    customer_name: str | None
    customer_phone: str | None
    customer_email: str | None
    item_summary: str | None = None
    total_amount: Decimal
    checkout_url: str | None
    checkout_created_at: datetime | None
    is_recovered: bool
    assignment_id: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None
    assigned_to_name: str | None = None
    call_status: TelecallingStatus | None = None
    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    lead_category: LeadCategory = LeadCategory.ABANDONED_CHECKOUT
    priority: LeadPriority | None = None


class CheckoutAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checkout_id: uuid.UUID
    assigned_to: uuid.UUID
    assigned_by: uuid.UUID | None
    assigned_at: datetime
    team_leader_id: uuid.UUID | None
    assignment_status: str
    reassigned_from: uuid.UUID | None
    reassigned_to: uuid.UUID | None
    reassigned_at: datetime | None
    reassignment_reason: str | None
    current_status: TelecallingStatus
    attempt_count: int
    last_attempt_at: datetime | None
    next_follow_up_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CheckoutCallAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checkout_id: uuid.UUID
    telecaller_id: uuid.UUID | None
    attempt_number: int
    attempted_at: datetime
    outcome: TelecallingStatus
    notes: str | None
    next_follow_up_at: datetime | None
    created_at: datetime


class CallHistoryEntryResponse(CallAttemptResponse):
    """One row of the Telecaller's "Call History" page — a `CallAttempt`
    plus the order number, since that page spans every order the
    telecaller has ever called (unlike the per-order call history on the
    order-detail page, which already has the order in context).
    """

    order_number: str


class TelecallerOptionResponse(BaseModel):
    """One selectable entry for a "Select Telecaller" assignment dropdown
    — deliberately just the User fields an assignment UI needs, not a
    second Telecaller data source: sourced straight from `User`/`Role`
    (Administration -> Users), independent of whether that telecaller
    has ever had a lead assigned yet.
    """

    id: uuid.UUID
    name: str
    email: str


class TelecallerPerformanceResponse(BaseModel):
    telecaller_id: uuid.UUID
    telecaller_name: str
    assigned: int
    called: int
    pending: int = 0
    connected: int
    interested: int = 0
    follow_ups: int
    # Call-outcome confirmed (`TelecallingStatus.CONFIRMED`, from a logged
    # call attempt) — NOT the same fact as `orders_confirmed` below
    # (`Order.status == CONFIRMED`, set by the telecaller Confirm action).
    # Deliberately separate fields, never merged, per the "don't mix
    # TelecallingStatus/OrderStatus/ShipmentStatus" rule.
    confirmed: int
    not_interested: int
    # Percentage (0-100, one decimal) of assigned leads marked CONFIRMED —
    # this codebase's existing enum has no separate "Converted" status
    # (spec's suggested outcome list, reused as-is per the "don't create
    # duplicate outcome systems" rule); CONFIRMED is treated as the
    # converted-lead signal.
    conversion_rate: float = 0.0
    # Live snapshot counts from `Order.confirmed_by_telecaller_id` /
    # `Shipment.current_status` — see
    # `OrderRepository.telecaller_confirmation_counts`'s docstring.
    orders_confirmed: int = 0
    shipped: int = 0
    delivered: int = 0


class TelecallerDetailSummaryResponse(BaseModel):
    """Summary cards for one telecaller's individual performance page —
    `TelecallerPerformanceResponse`'s fields plus `cancelled` and
    `fulfilled` (a live count, not a historical one — see
    `TelecallingService.telecaller_detail_summary`'s docstring for why no
    day-bucketed fulfillment figure is offered).
    """

    telecaller_id: uuid.UUID
    telecaller_name: str
    assigned: int
    called: int
    pending: int = 0
    connected: int
    interested: int = 0
    follow_ups: int
    confirmed: int
    not_interested: int
    cancelled: int = 0
    fulfilled: int = 0
    # Real total call-attempt count (an order called 3 times counts as 3
    # here, not 1) — distinct from `called`, which is a per-order "has
    # this order been called at least once" count. See
    # `OrderAssignmentRepository.total_attempt_count`'s docstring.
    total_attempts: int = 0
    # `total_attempts / orders_with_attempts` -- the dashboard's "Average
    # Call Attempts" tile. 0.0 when nothing has been called yet. See
    # `OrderAssignmentRepository.attempt_averaging_stats`'s docstring.
    average_call_attempts: float = 0.0
    conversion_rate: float = 0.0
    # Live snapshot counts from `Order.confirmed_by_telecaller_id` /
    # `Shipment.current_status` — see
    # `OrderRepository.telecaller_confirmation_counts`'s docstring for why
    # these are current-state counts, not a day-bucketed history.
    orders_confirmed: int = 0
    shipped: int = 0
    delivered: int = 0
    ndr: int = 0
    rto: int = 0


class TelecallerDailyPerformancePoint(BaseModel):
    date: str
    attempts: int
    connected: int
    confirmed: int
    not_interested: int
    cancelled: int
    follow_ups: int


class TelecallingSummaryResponse(BaseModel):
    total_leads: int = 0
    unassigned_leads: int = 0
    assigned: int
    pending: int
    called: int
    connected: int
    follow_ups_today: int
    confirmed: int
    not_interested: int = 0
    abandoned_checkouts: int = 0
    cod_unfulfilled: int = 0
    cod_fulfilled: int = 0
    prepaid: int = 0
