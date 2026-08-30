"""Payment and PaymentTransaction.

Payment information may originate from Shopify or a payment provider.
`Payment` rows are created by `OrderService.create_order()` alongside a
manually-created order, and — starting the Cashfree integration — also
created/looked-up on demand by `app.services.cashfree_payment_service.
CashfreePaymentService` when a checkout session is initiated for any
order (`(source_system="cashfree", external_id=<deterministic Cashfree
order_id>)`, the same generic `SyncMetadataMixin` identity every other
provider-synced row already uses — see `BaseRepository.
upsert_by_external_id`). `PaymentTransaction` is an append-only per-event
log (never updated) of every gateway callback/lookup applied to a
`Payment` — one row per Cashfree webhook delivery or reconciliation
check that successfully resolves to a `Payment`, mirroring
`ShipmentEvent`'s append-only convention.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PaymentStatus, PaymentType, sa_enum
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.order import Order


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_payments_source_external_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_type: Mapped[PaymentType] = mapped_column(
        sa_enum(PaymentType, "payment_type"), nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        sa_enum(PaymentStatus, "payment_status"),
        nullable=False,
        default=PaymentStatus.PENDING,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="INR", server_default="INR"
    )
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    payment_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    order: Mapped[Order] = relationship(back_populates="payments")
    transactions: Mapped[list[PaymentTransaction]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentTransaction(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        # Defense-in-depth idempotency backstop, on top of the generic
        # WebhookEvent-level dedup every provider webhook already goes
        # through (spec: "ensure uniqueness/idempotency around provider
        # identifiers"). NULL `gateway_transaction_id` values never
        # collide (standard SQL multi-column unique semantics), so this
        # never constrains a transaction recorded without one.
        UniqueConstraint(
            "gateway", "gateway_transaction_id", name="uq_payment_transactions_gateway_txn"
        ),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gateway: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gateway_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    status: Mapped[PaymentStatus] = mapped_column(
        sa_enum(PaymentStatus, "payment_status"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Sanitized gateway response for this one event (e.g. Cashfree's
    # `data.payment`/`data.payment_gateway_details`/`error_details`) —
    # deliberately never the raw webhook body's `customer_details`, to
    # avoid a second copy of customer PII beyond what `WebhookEvent.
    # payload` already unavoidably stores (spec §11/§18: minimize PII
    # duplication where the data isn't needed).
    raw_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )

    payment: Mapped[Payment] = relationship(back_populates="transactions")
