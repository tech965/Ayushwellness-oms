"""Legacy order-placement inventory reservation.

Deliberately separate from `app.models.inventory_architecture.
InventoryReservation` (the dormant API-first shadow architecture's own
reservation table, FK'd to `order_workflow.id`) -- this table is FK'd
directly to the ACTIVE legacy `orders.id`/`product_variants.id` and backs
`app.services.order_reservation_service.LegacyOrderReservationService`,
used by `OrderService.create_order`/`upsert_synced_order` and
`InventoryService.apply_dispatch`. Never read or written by the dormant
architecture, and vice versa -- two independent "soft hold" ledgers over
the same `ProductVariant.available_quantity` column, kept apart exactly
as every other legacy/shadow pairing in this codebase is.

Units are boxes, the same unit as `ProductVariant.available_quantity`:
  physical              = ProductVariant.available_quantity (on hand)
  reserved               = SUM(status='active' reservations for a variant)
  available_to_promise   = physical - reserved
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

RESERVATION_ACTIVE = "active"
RESERVATION_RELEASED = "released"
RESERVATION_CONSUMED = "consumed"


class OrderInventoryReservation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_inventory_reservations"
    __table_args__ = (
        UniqueConstraint(
            "product_variant_id",
            "order_id",
            name="uq_order_inventory_reservations_variant_order",
        ),
        CheckConstraint("quantity > 0", name="ck_order_inventory_reservations_quantity_positive"),
        CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name="ck_order_inventory_reservations_status",
        ),
        Index(
            "ix_order_inventory_reservations_variant_status", "product_variant_id", "status"
        ),
    )

    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    # In boxes, the same unit as `ProductVariant.available_quantity`.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RESERVATION_ACTIVE, server_default=RESERVATION_ACTIVE
    )
