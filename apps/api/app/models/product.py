"""Product and ProductVariant.

Primary source is Shopify starting Phase 2. SKU carries the uniqueness
constraint operators actually search by; `SyncMetadataMixin` +
`shopify_variant_id` carry the sync-idempotency constraint.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProductStatus, sa_enum
from app.models.mixins import SyncMetadataMixin


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_products_source_external_id"),
    )

    shopify_product_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Optional OMS-authoritative display name. When set, the Inventory UI
    # shows this instead of `title`; `title` still mirrors Shopify exactly.
    # Never written by a sync -- `ShopifyProductNormalizer` doesn't emit it,
    # so `upsert_synced_product` can't overwrite a staff edit (same
    # protection-by-omission as `ProductVariant.available_quantity`).
    title_override: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProductStatus] = mapped_column(
        sa_enum(ProductStatus, "product_status"), nullable=False, default=ProductStatus.ACTIVE
    )
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tags: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductVariant(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "external_id", name="uq_product_variants_source_external_id"
        ),
        CheckConstraint("packets_per_box > 0", name="ck_product_variants_packets_per_box_positive"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shopify_variant_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    sku: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional OMS-authoritative display name -- see `Product.title_override`.
    # Shown in the Inventory UI instead of `title` when set; never touched
    # by a Shopify resync (the normalizer doesn't produce this key).
    title_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    compare_at_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    inventory_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # OMS-authoritative stock count, in BOXES (exposed as `available_boxes`
    # at the API/UI layer -- kept as `available_quantity` at the DB/model
    # layer to avoid an unnecessary rename migration). Unlike
    # `inventory_quantity` above (a passive Shopify mirror, overwritten on
    # every product sync), this is seeded from Shopify once at first sync
    # and afterwards only ever moved by `InventoryService` (dispatch
    # decrement, RTO restock, manual adjustment) via `InventoryMovement`.
    # Never rewritten by a resync -- see `ProductService.upsert_synced_product`.
    # Shopify's `inventory_quantity` is NEVER used in any box/stock
    # calculation -- see `InventoryService` module docstring.
    available_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # How many packets make up one box for THIS variant -- deliberately
    # per-variant, never a global constant (different products/variants
    # pack differently). Defaults to 1 for every pre-existing row so a
    # variant with no real pack-size configured yet behaves exactly as
    # before (1 packet == 1 box) rather than silently reinterpreting its
    # existing `available_quantity`. Changing this value only changes the
    # packets<->boxes conversion/display -- it never itself moves
    # `available_quantity` (see `InventoryService.update_packets_per_box`).
    packets_per_box: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    options: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[ProductStatus] = mapped_column(
        sa_enum(ProductStatus, "product_status"), nullable=False, default=ProductStatus.ACTIVE
    )

    product: Mapped[Product] = relationship(back_populates="variants")
