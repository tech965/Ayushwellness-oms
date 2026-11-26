"""InventoryMovement — append-only stock ledger.

Every change to `ProductVariant.available_quantity` is backed by exactly
one row here (never updated or deleted, same convention as `OrderEvent`/
`ShipmentEvent`). Idempotency for the automatic movement types (DISPATCH,
RTO_RESTOCK) is enforced two ways -- see `app.services.inventory_service`'s
module docstring for the full reasoning:
  1. `InventoryMovementRepository.get_for_order`/`exists_for_order`/
     `exists_for_rto`: a snapshot-time check before writing, sufficient
     against sequential re-fires (a shipment advancing through several
     statuses).
  2. The two PARTIAL unique indexes below: the actual safety net against
     two CONCURRENT transactions both passing check #1 before either
     commits.
       - `uq_inventory_movements_dispatch_variant_order`: one DISPATCH
         per (product_variant_id, order_id) -- unchanged from the
         original single 3-column constraint's DISPATCH behaviour, just
         scoped to DISPATCH rows only.
       - `uq_inventory_movements_rto_restock_variant_rto`: one RTO_RESTOCK
         per (product_variant_id, rto_id), NOT per order -- a receipt-level
         (one `RTO` row = one courier receipt, see `app.models.rto.RTO`)
         idempotency key, so a second shipment/RTO on the same order and
         variant can still restock its own, separate receipt once an
         order's `DISPATCH` row is read back against the SUM of every
         RTO_RESTOCK already recorded for that (variant, order) -- see
         `InventoryService.apply_rto_restock`/`verify_warehouse_receipt`.
     Neither index applies to MANUAL_ADJUSTMENT/INITIAL_STOCK rows (they
     aren't WHERE-matched by either), same as the original constraint's
     NULL-order_id exemption.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, UUIDPrimaryKeyMixin
from app.models.enums import InventoryMovementType, sa_enum

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.product import ProductVariant


class InventoryMovement(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        Index(
            "uq_inventory_movements_dispatch_variant_order",
            "product_variant_id",
            "order_id",
            unique=True,
            postgresql_where=text("movement_type = 'dispatch'"),
            sqlite_where=text("movement_type = 'dispatch'"),
        ),
        Index(
            "uq_inventory_movements_rto_restock_variant_rto",
            "product_variant_id",
            "rto_id",
            unique=True,
            postgresql_where=text("movement_type = 'rto_restock'"),
            sqlite_where=text("movement_type = 'rto_restock'"),
        ),
    )

    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movement_type: Mapped[InventoryMovementType] = mapped_column(
        sa_enum(InventoryMovementType, "inventory_movement_type"), nullable=False, index=True
    )
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)

    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("order_workflow.id", ondelete="SET NULL"), nullable=True, index=True
    )
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipment_refs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rto_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rtos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True, index=True
    )

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    product_variant: Mapped[ProductVariant] = relationship()
    actor: Mapped[User | None] = relationship()
