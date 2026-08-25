"""Return. Named `returns.py` (not `return.py`) to avoid shadowing the
`return` keyword.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ReturnStatus, sa_enum
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.order import Order, OrderItem
    from app.models.refund import Refund


class Return(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "returns"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_returns_source_external_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ReturnStatus] = mapped_column(
        sa_enum(ReturnStatus, "return_status"),
        nullable=False,
        default=ReturnStatus.REQUESTED,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    requested_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped[Order] = relationship()
    order_item: Mapped[OrderItem | None] = relationship()
    customer: Mapped[Customer | None] = relationship()
    refunds: Mapped[list[Refund]] = relationship(back_populates="return_")
