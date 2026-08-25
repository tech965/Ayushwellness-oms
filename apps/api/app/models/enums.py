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
