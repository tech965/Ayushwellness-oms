"""OMS-owned inventory architecture (provider-independent).

* `product_variants.id` / `.sku` remain the OMS-owned internal variant
  identity; physical stock stays in `product_variants.available_quantity`
  (the on-hand quantity in boxes) with the ledger in `inventory_movements`.
* `provider_variant_mappings` links an internal variant to a provider's
  variant by (provider, external id). Nothing about inventory is keyed on a
  Shopify id, so Amazon / Flipkart / Meesho can later be mapped to the same
  internal variant by adding rows with another `provider` value.
* `inventory_reservations` holds stock set aside for an order (derived
  `reserved = SUM(active)`); `available_to_promise = physical - reserved`.
* `warehouses` names where stock sits. Every movement and reservation
  carries a warehouse; one default warehouse exists today.

None of these tables stores a provider snapshot: mappings hold identifiers
only; catalogue data (titles, prices, images) is read live.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

DEFAULT_WAREHOUSE_CODE = "MAIN"

RESERVATION_ACTIVE = "active"
RESERVATION_RELEASED = "released"
RESERVATION_CONSUMED = "consumed"


class Warehouse(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class ProviderVariantMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "provider_variant_mappings"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_variant_id", name="uq_provider_variant_mappings_external"
        ),
        UniqueConstraint("provider", "variant_id", name="uq_provider_variant_mappings_variant"),
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    external_product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_variant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )


class InventoryReservation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        UniqueConstraint("variant_id", "order_id", name="uq_inventory_reservations_variant_order"),
        CheckConstraint("quantity > 0", name="ck_inventory_reservations_quantity_positive"),
        CheckConstraint(
            "status IN ('active', 'released', 'consumed')", name="ck_inventory_reservations_status"
        ),
        Index("ix_inventory_reservations_variant_status", "variant_id", "status"),
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_workflow.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    # In boxes, the same unit as `ProductVariant.available_quantity`.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RESERVATION_ACTIVE, server_default=RESERVATION_ACTIVE
    )
