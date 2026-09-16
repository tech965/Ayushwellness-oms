"""add platform_stock_movements (multi-platform manual inventory ledger)

Revision ID: 4fbb5db3fa34
Revises: d4f8a3c1e6b9
Create Date: 2026-09-17 00:00:00.000000

Adds `platform_stock_movements`: an append-only, date-wise manual
marketplace stock ledger (Amazon / Flipkart / Blinkit / Meesho / Manual-
Other) -- see `app.models.platform_inventory.PlatformStockMovement` for
the full design rationale.

Why a NEW table instead of extending `inventory_movements`:
`inventory_movements` is Shopify/dispatch-shaped by construction (its
`order_id`/`shipment_id`/`rto_id` columns and its
`UniqueConstraint("product_variant_id", "order_id", "movement_type")`
exist specifically for Shiprocket-driven DISPATCH/RTO_RESTOCK
idempotency). It has no platform/channel column and no day-bucketing
concept, and Shopify inventory must remain fully automatic and untouched
by this feature -- so this migration adds an isolated, parallel table
instead of altering `inventory_movements`, `product_variants`, or any
other existing table at all.

STRICTLY SCHEMA ONLY. Creates one new table, its enum type, and its
indexes -- zero rows are inserted, and no existing table is altered.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4fbb5db3fa34"
down_revision: str | None = "d4f8a3c1e6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_stock_movements",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("product_variant_id", app.db.base.GUID(), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column(
            "movement_type",
            sa.Enum("stock_added", "stock_deducted", name="platform_stock_movement_type"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_platform_stock_movements_product_variant_id"),
        "platform_stock_movements",
        ["product_variant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_platform_stock_movements_platform"),
        "platform_stock_movements",
        ["platform"],
        unique=False,
    )
    op.create_index(
        op.f("ix_platform_stock_movements_movement_type"),
        "platform_stock_movements",
        ["movement_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_platform_stock_movements_stock_date"),
        "platform_stock_movements",
        ["stock_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_platform_stock_movements_created_at"),
        "platform_stock_movements",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_platform_stock_movements_variant_platform_date",
        "platform_stock_movements",
        ["product_variant_id", "platform", "stock_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_stock_movements_variant_platform_date",
        table_name="platform_stock_movements",
    )
    op.drop_index(
        op.f("ix_platform_stock_movements_created_at"), table_name="platform_stock_movements"
    )
    op.drop_index(
        op.f("ix_platform_stock_movements_stock_date"), table_name="platform_stock_movements"
    )
    op.drop_index(
        op.f("ix_platform_stock_movements_movement_type"), table_name="platform_stock_movements"
    )
    op.drop_index(
        op.f("ix_platform_stock_movements_platform"), table_name="platform_stock_movements"
    )
    op.drop_index(
        op.f("ix_platform_stock_movements_product_variant_id"),
        table_name="platform_stock_movements",
    )
    op.drop_table("platform_stock_movements")
    sa.Enum(name="platform_stock_movement_type").drop(op.get_bind(), checkfirst=True)
