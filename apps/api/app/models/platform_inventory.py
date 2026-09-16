"""PlatformStockMovement — append-only, date-wise manual marketplace
stock ledger (Amazon / Flipkart / Blinkit / Meesho / Manual-Other).

Deliberately a SEPARATE table from `inventory_movements`
(`app.models.inventory.InventoryMovement`), never a repurposing of it:

  - `InventoryMovement` is Shopify/dispatch-shaped by construction (its
    `order_id`/`shipment_id`/`rto_id` columns and its
    `UniqueConstraint("product_variant_id", "order_id", "movement_type")`
    exist specifically to make DISPATCH/RTO_RESTOCK idempotent against a
    Shiprocket order — see that model's own docstring). It has no
    platform/channel column and no day-bucketing concept at all.
  - Shopify inventory must stay fully automatic and untouched by this
    feature (explicit requirement) — so this table is never written for
    Shopify; Shopify's own numbers for a unified view are read live from
    `ProductVariant.available_quantity` + `InventoryMovement` instead
    (see `app.services.platform_inventory_service`).

Same append-only, `quantity_delta` + `quantity_after` running-balance
shape as `InventoryMovement` (so `PlatformInventoryService.record_movement`
can follow the exact same safe, additive-only, never-trust-a-client-total
pattern as `InventoryService.add_stock`), plus two columns
`InventoryMovement` doesn't need:

  - `platform`: a plain string (like `SourceSystem` in `app.models.mixins`
    -- deliberately NOT a DB enum), so a new marketplace is added by
    extending `InventoryPlatform.ALL` below, never a migration.
  - `stock_date`: the IST calendar date (a `Date`, not a timestamp) this
    movement is attributed to -- what makes "stock as of 11 Sep" and
    "movements during 11 Sep" both answerable, and what keeps a past
    day's entry from ever being silently overwritten by a later one
    (each day's entries are their own rows; "the closing balance for
    day D" is simply the latest row with `stock_date &lt;= D`).

No `UniqueConstraint` on `(product_variant_id, platform, stock_date)`:
unlike DISPATCH/RTO_RESTOCK (each driven by exactly one Shiprocket event),
a warehouse user may legitimately record more than one Add-Stock/
Record-Sale action for the same platform on the same day -- this ledger
stays append-only and unrestricted the same way `MANUAL_ADJUSTMENT` rows
on `InventoryMovement` already are.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, UUIDPrimaryKeyMixin
from app.models.enums import PlatformStockMovementType, sa_enum

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.product import ProductVariant


class InventoryPlatform:
    """Known `PlatformStockMovement.platform` values. Plain strings, not a
    DB enum -- mirrors `app.models.mixins.SourceSystem`'s existing
    "add a platform without a migration" convention exactly.

    Deliberately does NOT include "shopify" -- Shopify inventory is never
    written to this table (see module docstring); it's a distinct,
    always-automatic data source.
    """

    AMAZON = "amazon"
    FLIPKART = "flipkart"
    BLINKIT = "blinkit"
    MEESHO = "meesho"
    MANUAL_OTHER = "manual_other"

    ALL = (AMAZON, FLIPKART, BLINKIT, MEESHO, MANUAL_OTHER)

    LABELS = {
        AMAZON: "Amazon",
        FLIPKART: "Flipkart",
        BLINKIT: "Blinkit",
        MEESHO: "Meesho",
        MANUAL_OTHER: "Manual / Other",
    }


class PlatformStockMovement(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "platform_stock_movements"
    __table_args__ = (
        Index(
            "ix_platform_stock_movements_variant_platform_date",
            "product_variant_id",
            "platform",
            "stock_date",
        ),
    )

    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    movement_type: Mapped[PlatformStockMovementType] = mapped_column(
        sa_enum(PlatformStockMovementType, "platform_stock_movement_type"),
        nullable=False,
        index=True,
    )
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_date: Mapped[date_type] = mapped_column(Date(), nullable=False, index=True)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    product_variant: Mapped[ProductVariant] = relationship()
    actor: Mapped[User | None] = relationship()
