"""Refund.

`return_id` is nullable — refunds can exist without a return (e.g. an
order cancellation refund). In Phase 1 the only creation path is
`ReturnService` marking a `Return` `COMPLETED`; no live payment gateway is
connected, so the API exposes read endpoints only (spec §36).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RefundStatus, sa_enum
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.payment import Payment
    from app.models.returns import Return


class Refund(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_refunds_source_external_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    return_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("returns.id", ondelete="SET NULL"), nullable=True, index=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[RefundStatus] = mapped_column(
        sa_enum(RefundStatus, "refund_status"),
        nullable=False,
        default=RefundStatus.PENDING,
        index=True,
    )
    initiated_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    refund_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    order: Mapped[Order] = relationship()
    payment: Mapped[Payment | None] = relationship()
    return_: Mapped[Return | None] = relationship(back_populates="refunds")
