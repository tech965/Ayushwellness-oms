"""NDR (Non-Delivery Report) foundation.

Will primarily originate from Shiprocket/couriers starting Phase 2.
Courier-specific reason strings are never assumed universal — `reason` is
the OMS-normalized bucket, `external_reason` is the raw courier text.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NDRStatus, sa_enum
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.courier import Courier
    from app.models.order import Order
    from app.models.shipment import Shipment


class NDR(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "ndrs"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_ndrs_source_external_id"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    courier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("couriers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_reason: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    external_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    status: Mapped[NDRStatus] = mapped_column(
        sa_enum(NDRStatus, "ndr_status"), nullable=False, default=NDRStatus.OPEN, index=True
    )
    customer_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    reattempt_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reattempt_date: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    shipment: Mapped[Shipment] = relationship()
    order: Mapped[Order] = relationship()
    courier: Mapped[Courier | None] = relationship()
