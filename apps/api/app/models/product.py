"""Product and ProductVariant.

Primary source is Shopify starting Phase 2. SKU carries the uniqueness
constraint operators actually search by; `SyncMetadataMixin` +
`shopify_variant_id` carry the sync-idempotency constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProductStatus, sa_enum
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.auth import User


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
    # Shopify's `featuredImage.url` -- pulled by the same product pull-
    # sync as everything else above (`ShopifyProductNormalizer`), used to
    # show a real product thumbnail next to order line items (e.g. the
    # Telecaller order detail page) instead of just a SKU/name. `None`
    # for a product with no Shopify image set, or one never synced from
    # Shopify (a manually-created OMS product) -- the UI falls back to a
    # placeholder, never a broken image.
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
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
    catalog_variants: Mapped[list[CatalogVariant]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="CatalogVariant.display_order, CatalogVariant.name",
    )


class CatalogVariant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """OMS-visible catalog variant -- a presentation/grouping layer over
    one or more underlying Shopify `ProductVariant` rows.

    The OMS UI shows CatalogVariants, NOT raw Shopify variants: "Aayush
    Herbal Masala" has 3 CatalogVariants (one per flavour), each grouping
    its 60/120/180-pouch `ProductVariant` rows; every other product has 1
    CatalogVariant grouping all its pack-size `ProductVariant` rows.

    This layer is presentation only. Orders, Shiprocket dispatch, RTO,
    `InventoryMovement`, and all idempotency still reference
    `ProductVariant` unchanged. `ProductVariant.available_quantity` is
    still the sole authoritative stock; read APIs merely SUM it across a
    CatalogVariant's members. Nothing here ever moves stock.

    Grouping assignments (`ProductVariant.catalog_variant_id`) are set by
    an explicit, reviewed data operation -- never guessed, never written
    by Shopify sync (the normalizer doesn't emit that key, so
    `ProductService.upsert_synced_product` cannot touch it, same
    protection-by-omission as `available_quantity` / `title_override`).
    """

    __tablename__ = "catalog_variants"
    __table_args__ = (
        UniqueConstraint("product_id", "name", name="uq_catalog_variants_product_name"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # English OMS display name shown in the Inventory UI (e.g. "Ghutka
    # Flavour"). Editable via the Inventory "Edit Name" action; never
    # sourced from or overwritten by Shopify.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    product: Mapped[Product] = relationship(back_populates="catalog_variants")
    product_variants: Mapped[list[ProductVariant]] = relationship(back_populates="catalog_variant")


class CatalogVariantStockAdjustment(Base, UUIDPrimaryKeyMixin):
    """Append-only ledger for a manual "Total Stock" edit made directly
    against a `CatalogVariant` (e.g. Blue Packet's combined 60/120/180
    total), never against a single underlying `ProductVariant`.

    Deliberately separate from `InventoryMovement`, which is documented
    and constrained as backing exactly one `ProductVariant`'s own
    balance (`product_variant_id` NOT NULL) -- a CatalogVariant total
    edit is NOT attributed to any one pack-size SKU, so it cannot be
    expressed as a row there without violating that invariant.

    `available_boxes` for a CatalogVariant = SUM(underlying
    ProductVariant.available_quantity) + SUM(this table's
    quantity_delta for that catalog_variant_id) -- see
    `app.api.v1.endpoints.inventory._oms_variant_response`. This extra
    amount is a reconciliation total, not stock attached to a specific
    sellable SKU: dispatch/RTO/Shopify sync never read or write this
    table, and no `ProductVariant` row is ever touched by it.
    """

    __tablename__ = "catalog_variant_stock_adjustments"

    catalog_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    catalog_variant: Mapped[CatalogVariant] = relationship()
    actor: Mapped[User | None] = relationship()


class ProductVariant(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "external_id", name="uq_product_variants_source_external_id"
        ),
        CheckConstraint("packets_per_box > 0", name="ck_product_variants_packets_per_box_positive"),
        CheckConstraint("pack_size > 0", name="ck_product_variants_pack_size_positive"),
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
    # How many packets/pouches ONE unit of THIS variant (as ordered --
    # `OrderItem.quantity` counts units of this specific SKU, e.g. "1" for
    # one 120-Pack bundle purchased) actually contains -- deliberately
    # per-variant, never a global constant. Combined with `packets_per_box`
    # above, `InventoryService` converts an order line to boxes as
    # `ceil(quantity * pack_size / packets_per_box)`, so a 120-Pack variant
    # (pack_size=120) against a 60-pouch box (packets_per_box=60) correctly
    # deducts 2 boxes per unit sold, not 1. Defaults to 1 for every
    # pre-existing row so a variant with no real pack size configured yet
    # behaves exactly as before (1 unit ordered == 1 packet == today's
    # existing ceil(quantity/packets_per_box) formula, unchanged). Changing
    # this value only changes future dispatch/RTO box math -- it never
    # itself moves `available_quantity` or rewrites a past `InventoryMovement`.
    pack_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    options: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[ProductStatus] = mapped_column(
        sa_enum(ProductStatus, "product_status"), nullable=False, default=ProductStatus.ACTIVE
    )
    # OMS-visible grouping (presentation only). NULL == not yet grouped:
    # such a variant is surfaced as its own implicit single-member OMS
    # variant, exactly as before this layer existed. Set only by an
    # explicit reviewed data operation; `ondelete="SET NULL"` so removing
    # a CatalogVariant only un-groups its members -- it never deletes a
    # ProductVariant or its movement ledger. Never written by Shopify sync.
    catalog_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Shopify's per-variant image (`ProductVariant.image.url` on the
    # GraphQL Admin API, or the matching entry in a REST webhook's
    # `images[]` looked up by `image_id`) -- set only when Shopify has
    # actually assigned a distinct image to THIS variant (e.g. one photo
    # per flavour). `None` for a variant that shares the product's
    # featured image; the read side (`InventoryService`/the Inventory API)
    # falls back to `Product.image_url` in that case, never guessing an
    # association Shopify didn't provide. Same sync contract as
    # `Product.image_url`: updated when Shopify has a value, left alone
    # (never nulled) when a sync payload has none, so a transient missing
    # image never erases a previously-known one.
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    product: Mapped[Product] = relationship(back_populates="variants")
    catalog_variant: Mapped[CatalogVariant | None] = relationship(back_populates="product_variants")
