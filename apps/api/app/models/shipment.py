"""Shipment and ShipmentEvent.

CRITICAL: `Shipment` holds CURRENT state only. `ShipmentEvent` is the
append-only tracking history — never updated, never deleted. Dedup is by
`(shipment_id, external_event_id)` when the courier provides a stable
event ID; when it doesn't, `ShipmentService.add_tracking_event` falls
back to a `(status, event_timestamp)` check.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NDRStatus, RTOStatus, ShipmentDelayStatus, ShipmentStatus, sa_enum
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.courier import Courier
    from app.models.order import Order


class Shipment(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_shipments_source_external_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shiprocket_shipment_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    awb: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    courier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("couriers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    current_status: Mapped[ShipmentStatus] = mapped_column(
        sa_enum(ShipmentStatus, "shipment_status"),
        nullable=False,
        default=ShipmentStatus.PENDING,
        index=True,
    )
    delay_status: Mapped[ShipmentDelayStatus] = mapped_column(
        sa_enum(ShipmentDelayStatus, "shipment_delay_status"),
        nullable=False,
        default=ShipmentDelayStatus.UNKNOWN,
    )
    ndr_status: Mapped[NDRStatus | None] = mapped_column(
        sa_enum(NDRStatus, "ndr_status"), nullable=True
    )
    rto_status: Mapped[RTOStatus | None] = mapped_column(
        sa_enum(RTOStatus, "rto_status"), nullable=True
    )

    pickup_date: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    expected_delivery_date: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True, index=True
    )
    actual_delivery_date: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    current_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_tracking_update_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    order: Mapped[Order] = relationship(back_populates="shipments")
    courier: Mapped[Courier | None] = relationship(back_populates="shipments")
    events: Mapped[list[ShipmentEvent]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        order_by="ShipmentEvent.event_timestamp",
    )


class ShipmentEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only tracking timeline. No `updated_at`, no update/delete
    repository methods — see the module docstring.
    """

    __tablename__ = "shipment_events"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id", "external_event_id", name="uq_shipment_events_shipment_external_event"
        ),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(100), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    courier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="system", server_default="system"
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )

    shipment: Mapped[Shipment] = relationship(back_populates="events")
