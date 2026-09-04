"""Controlled vocabularies for status columns.

`docs/database/schema.md`'s "Conventions" section requires status columns
to use enums, not free text. `sqlalchemy.Enum` renders as a native
Postgres enum type in production and a `VARCHAR` + `CHECK` constraint on
SQLite, so the same model works against both the real database and the
in-memory test suite with no extra code.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum


def sa_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """One shared Postgres enum type per `name`, reused across every
    column/table that models the same status — not a fresh `CREATE TYPE`
    per column.
    """
    return Enum(
        enum_cls, name=name, native_enum=True, values_callable=lambda e: [x.value for x in e]
    )


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    PACKED = "packed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class FulfillmentStatus(StrEnum):
    UNFULFILLED = "unfulfilled"
    PARTIAL = "partial"
    FULFILLED = "fulfilled"


class CancellationStatus(StrEnum):
    NONE = "none"
    REQUESTED = "requested"
    CANCELLED = "cancelled"


class PaymentType(StrEnum):
    COD = "cod"
    PREPAID = "prepaid"
    OTHER = "other"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class ShipmentStatus(StrEnum):
    PENDING = "pending"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    NDR = "ndr"
    RTO_INITIATED = "rto_initiated"
    RTO_DELIVERED = "rto_delivered"
    CANCELLED = "cancelled"


class ShipmentDelayStatus(StrEnum):
    ON_TIME = "on_time"
    AT_RISK = "at_risk"
    DELAYED = "delayed"
    UNKNOWN = "unknown"


class NDRStatus(StrEnum):
    OPEN = "open"
    CUSTOMER_CONTACTED = "customer_contacted"
    REATTEMPT_SCHEDULED = "reattempt_scheduled"
    RESOLVED = "resolved"
    RTO_INITIATED = "rto_initiated"


class RTOStatus(StrEnum):
    INITIATED = "initiated"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AddressType(StrEnum):
    SHIPPING = "shipping"
    BILLING = "billing"
    OTHER = "other"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    ARCHIVED = "archived"


class IntegrationType(StrEnum):
    ECOMMERCE = "ecommerce"
    COURIER = "courier"
    MESSAGING = "messaging"
    SOCIAL = "social"


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    SYNCING = "syncing"
    DISABLED = "disabled"


class SyncType(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
    WEBHOOK = "webhook"


class SyncJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WebhookEventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


class ReconciliationRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReconciliationStatus(StrEnum):
    """Per-check result — spec §11: RECONCILED / MISMATCH / MISSING / ERROR."""

    RECONCILED = "reconciled"
    MISMATCH = "mismatch"
    MISSING = "missing"
    ERROR = "error"


class AssignmentStatus(StrEnum):
    """An order has at most one ACTIVE `OrderAssignment` at a time — a
    reassignment flips the old row to INACTIVE and inserts a new ACTIVE
    row, never mutating history (see `TelecallingService.reassign_order`).
    """

    ACTIVE = "active"
    INACTIVE = "inactive"


class TelecallingStatus(StrEnum):
    """Telecaller-facing calling status — deliberately separate from
    `OrderStatus` (the pack/ship operational workflow). Used both as
    `OrderAssignment.current_status` (the order's latest calling state)
    and `CallAttempt.outcome` (what happened on one specific call) — the
    API layer never accepts NOT_CALLED as a loggable call outcome, it's
    only ever a default/initial state.
    """

    NOT_CALLED = "not_called"
    CALL_ATTEMPTED = "call_attempted"
    CONNECTED = "connected"
    NOT_RECEIVED = "not_received"
    BUSY = "busy"
    SWITCHED_OFF = "switched_off"
    INVALID_NUMBER = "invalid_number"
    CALL_BACK_REQUESTED = "call_back_requested"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    FOLLOW_UP_REQUIRED = "follow_up_required"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class LeadCategory(StrEnum):
    """Which calling-opportunity bucket a telecalling lead belongs to —
    derived, never stored redundantly: an order-based lead's category is
    computed straight from its `Order.payment_type`/`fulfillment_status`
    (see `app.services.lead_classification.classify_order`) so it can
    never drift from the order it describes; `ABANDONED_CHECKOUT` is the
    one category with its own dedicated source (`AbandonedCheckout`,
    `app.models.abandoned_checkout`), since it has no backing `Order` at
    all until/unless the customer completes the purchase.

    Deliberately excludes a plain "Abandoned Cart" bucket — Shopify's
    Admin API exposes no customer-identifiable data for a cart that never
    reached checkout, and this codebase never fabricates a callable lead
    without a real phone number to call (see the module docstring on
    `AbandonedCheckout`).
    """

    ABANDONED_CHECKOUT = "abandoned_checkout"
    COD_UNFULFILLED = "cod_unfulfilled"
    COD_FULFILLED = "cod_fulfilled"
    PREPAID = "prepaid"


class LeadPriority(StrEnum):
    """Computed display/sort hint for a telecalling lead — never a
    persisted column (see `app.services.lead_classification.classify_priority`),
    so it always reflects the lead's *current* category/follow-up state
    rather than a snapshot that could go stale.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
