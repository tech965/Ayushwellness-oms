"""RTO (Return to Origin) foundation. See `app/models/ndr.py` docstring
for the reason/normalized_reason/external_reason convention.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RTOStatus, sa_enum
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.courier import Courier
    from app.models.order import Order
    from app.models.shipment import Shipment


class RTO(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "rtos"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_rtos_source_external_id"),
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
    status: Mapped[RTOStatus] = mapped_column(
        sa_enum(RTOStatus, "rto_status"), nullable=False, default=RTOStatus.INITIATED, index=True
    )
    initiated_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rto_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    shipment: Mapped[Shipment] = relationship()
    order: Mapped[Order] = relationship()
    courier: Mapped[Courier | None] = relationship()
