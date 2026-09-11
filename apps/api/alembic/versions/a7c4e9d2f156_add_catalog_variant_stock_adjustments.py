"""add catalog_variant_stock_adjustments (CatalogVariant total-stock ledger)

Revision ID: a7c4e9d2f156
Revises: eae1a61e30b7
Create Date: 2026-09-11 00:00:00.000000

Adds `catalog_variant_stock_adjustments`: a ledger for a manual "Total
Stock" edit made directly against an OMS-visible `CatalogVariant` (e.g.
Blue Packet) rather than against any single underlying `ProductVariant`
SKU (60/120/180).

Why a separate table instead of reusing `inventory_movements`:
`inventory_movements` is documented and constrained as backing EXACTLY
ONE `ProductVariant`'s own balance, 1:1 (`product_variant_id` is
NOT NULL, and `quantity_after` is that one row's own running balance --
see `app.models.inventory.InventoryMovement`). A CatalogVariant-level
total edit is deliberately NOT attributed to any single underlying SKU
(there is no non-arbitrary way to decide which pack size a generic
total change belongs to), so it cannot be expressed as a row in that
table without violating its own invariant. This migration therefore
adds an isolated, parallel ledger instead of altering
`inventory_movements` at all.

What this total means: `available_boxes` for a CatalogVariant becomes
`SUM(underlying ProductVariant.available_quantity) + SUM(this table's
quantity_delta for that catalog_variant_id)`. The extra amount is a
reconciliation total, not stock attached to a specific sellable SKU --
dispatch/RTO continue to read and write only real `ProductVariant` rows,
completely unaware of this table.

STRICTLY SCHEMA ONLY. This migration creates the table and its index/FK
only -- zero rows are inserted, and no existing table (`catalog_variants`,
`product_variants`, `inventory_movements`, orders, shipments, rtos) is
altered.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c4e9d2f156"
down_revision: str | None = "eae1a61e30b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_variant_stock_adjustments",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("catalog_variant_id", app.db.base.GUID(), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("quantity_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("actor_user_id", app.db.base.GUID(), nullable=True),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["catalog_variant_id"], ["catalog_variants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_catalog_variant_stock_adjustments_catalog_variant_id"),
        "catalog_variant_stock_adjustments",
        ["catalog_variant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_catalog_variant_stock_adjustments_created_at"),
        "catalog_variant_stock_adjustments",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_catalog_variant_stock_adjustments_created_at"),
        table_name="catalog_variant_stock_adjustments",
    )
    op.drop_index(
        op.f("ix_catalog_variant_stock_adjustments_catalog_variant_id"),
        table_name="catalog_variant_stock_adjustments",
    )
    op.drop_table("catalog_variant_stock_adjustments")
