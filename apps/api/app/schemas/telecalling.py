from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import FulfillmentStatus, PaymentStatus, PaymentType, TelecallingStatus


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
    fulfillment_status: FulfillmentStatus
    order_datetime: datetime
    shipping_address: dict | None = None
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


class CallHistoryEntryResponse(CallAttemptResponse):
    """One row of the Telecaller's "Call History" page — a `CallAttempt`
    plus the order number, since that page spans every order the
    telecaller has ever called (unlike the per-order call history on the
    order-detail page, which already has the order in context).
    """

    order_number: str


class TelecallerPerformanceResponse(BaseModel):
    telecaller_id: uuid.UUID
    telecaller_name: str
    assigned: int
    called: int
    connected: int
    follow_ups: int
    confirmed: int
    not_interested: int


class TelecallingSummaryResponse(BaseModel):
    assigned: int
    pending: int
    called: int
    connected: int
    follow_ups_today: int
    confirmed: int
    not_interested: int = 0
