"""Payment and PaymentTransaction.

Payment information may originate from Shopify or a payment provider
starting Phase 2+. No live gateway is connected in Phase 1 — `Payment`
rows are created by `OrderService.create_order()` alongside the order,
and the API only exposes read endpoints (`docs` §36).
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
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )

    payment: Mapped[Payment] = relationship(back_populates="transactions")
