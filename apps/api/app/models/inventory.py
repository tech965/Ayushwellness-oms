"""InventoryMovement — append-only stock ledger.

Every change to `ProductVariant.available_quantity` is backed by exactly
one row here (never updated or deleted, same convention as `OrderEvent`/
`ShipmentEvent`). Idempotency for the automatic movement types (DISPATCH,
RTO_RESTOCK) is enforced in `InventoryService` by checking for an
existing row keyed on `(product_variant_id, movement_type, order_id)`
before writing a new one -- see `InventoryMovementRepository.exists_for_order`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, UUIDPrimaryKeyMixin
from app.models.enums import InventoryMovementType, sa_enum

if TYPE_CHECKING:
    from app.models.product import ProductVariant


class InventoryMovement(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "inventory_movements"

    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movement_type: Mapped[InventoryMovementType] = mapped_column(
        sa_enum(InventoryMovementType, "inventory_movement_type"), nullable=False, index=True
    )
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)

    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rto_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rtos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    product_variant: Mapped[ProductVariant] = relationship()
