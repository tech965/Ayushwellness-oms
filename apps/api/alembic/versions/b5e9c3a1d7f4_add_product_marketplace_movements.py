"""add product_marketplace_movements (product-level marketplace ledger)

Revision ID: b5e9c3a1d7f4
Revises: 4fbb5db3fa34
Create Date: 2026-09-18 00:00:00.000000

Adds `product_marketplace_movements`: an append-only, product-level
(never SKU-level) manual marketplace ledger backing "Record Sale" /
"Add Stock" / "RTO" on the Marketplace Stock table's ONE combined row
per platform -- see `app.models.platform_inventory.
ProductMarketplaceMovement` for the full design rationale.

Why a THIRD table, instead of reusing `platform_stock_movements`
(added by `4fbb5db3fa34`, one migration prior) or `inventory_movements`:
both existing ledgers are scoped to `product_variant_id` (a single real
SKU) by construction -- their whole point is a decision about WHICH SKU
a movement belongs to. This feature's business requirement is the
opposite: a marketplace sale/RTO/stock-add is entered as one product-
level quantity with NO SKU attached, and there is no non-arbitrary way
to retrofit that onto one of several real `ProductVariant` rows (see
`PlatformInventoryService.record_product_movement`'s uniform pack_size/
packets_per_box check, which REJECTS the write outright rather than
guess, whenever a product's SKUs disagree on pack_size). So this table
is scoped by `(product_id, platform)` instead, and never references
`product_variant_id` at all.

STRICTLY SCHEMA ONLY. Creates one new table, its enum type, and its
indexes -- zero rows are inserted, and no existing table
(`platform_stock_movements`, `inventory_movements`, `product_variants`,
`products`, `catalog_variants`, `catalog_variant_stock_adjustments`,
orders, shipments) is altered. `ProductVariant.available_quantity`,
`InventoryMovement`, `PlatformStockMovement`, and the existing
`CatalogVariantStockAdjustment` reconciliation ledger are all
completely untouched by this migration and by this feature's write
path -- this is a fourth, independent, additive-only ledger, not a
replacement for any of them.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e9c3a1d7f4"
down_revision: str | None = "4fbb5db3fa34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_marketplace_movements",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("product_id", app.db.base.GUID(), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column(
            "movement_type",
            sa.Enum("stock_added", "sale", "rto", name="product_marketplace_movement_type"),
            nullable=False,
        ),
        sa.Column("quantity_packets", sa.Integer(), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("quantity_after", sa.Integer(), nullable=False),
        sa.Column("stock_date", sa.Date(), nullable=False),
        sa.Column("actor_user_id", app.db.base.GUID(), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_product_marketplace_movements_product_id"),
        "product_marketplace_movements",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_product_marketplace_movements_platform"),
        "product_marketplace_movements",
        ["platform"],
        unique=False,
    )
    op.create_index(
        op.f("ix_product_marketplace_movements_movement_type"),
        "product_marketplace_movements",
        ["movement_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_product_marketplace_movements_stock_date"),
        "product_marketplace_movements",
        ["stock_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_product_marketplace_movements_created_at"),
        "product_marketplace_movements",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_product_marketplace_movements_product_platform_date",
        "product_marketplace_movements",
        ["product_id", "platform", "stock_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_marketplace_movements_product_platform_date",
        table_name="product_marketplace_movements",
    )
    op.drop_index(
        op.f("ix_product_marketplace_movements_created_at"),
        table_name="product_marketplace_movements",
    )
    op.drop_index(
        op.f("ix_product_marketplace_movements_stock_date"),
        table_name="product_marketplace_movements",
    )
    op.drop_index(
        op.f("ix_product_marketplace_movements_movement_type"),
        table_name="product_marketplace_movements",
    )
    op.drop_index(
        op.f("ix_product_marketplace_movements_platform"),
        table_name="product_marketplace_movements",
    )
    op.drop_index(
        op.f("ix_product_marketplace_movements_product_id"),
        table_name="product_marketplace_movements",
    )
    op.drop_table("product_marketplace_movements")
    sa.Enum(name="product_marketplace_movement_type").drop(op.get_bind(), checkfirst=True)
