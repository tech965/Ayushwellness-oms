"""add inventory tracking (available_quantity + inventory_movements)

Revision ID: a4e9d3c7f158
Revises: 9c1f4b6a2d70
Create Date: 2026-09-04 00:05:00.000000

Adds `product_variants.available_quantity` -- the new OMS-authoritative
stock count, seeded from each variant's last-known `inventory_quantity`
(the passive Shopify mirror) and from then on only ever moved by
`InventoryService` (dispatch decrement, RTO restock, manual adjustment).
`inventory_movements` is the append-only ledger backing every such move.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4e9d3c7f158"
down_revision: str | None = "9c1f4b6a2d70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_variants",
        sa.Column(
            "available_quantity", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.execute(
        "UPDATE product_variants SET available_quantity = inventory_quantity"
    )

    op.create_table(
        "inventory_movements",
        sa.Column("product_variant_id", app.db.base.GUID(), nullable=False),
        sa.Column(
            "movement_type",
            sa.Enum(
                "dispatch",
                "rto_restock",
                "manual_adjustment",
                "initial_stock",
                name="inventory_movement_type",
            ),
            nullable=False,
        ),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("quantity_after", sa.Integer(), nullable=False),
        sa.Column("order_id", app.db.base.GUID(), nullable=True),
        sa.Column("shipment_id", app.db.base.GUID(), nullable=True),
        sa.Column("rto_id", app.db.base.GUID(), nullable=True),
        sa.Column("actor_user_id", app.db.base.GUID(), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rto_id"], ["rtos.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_inventory_movements_product_variant_id"),
        "inventory_movements",
        ["product_variant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_movement_type"),
        "inventory_movements",
        ["movement_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_order_id"),
        "inventory_movements",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_shipment_id"),
        "inventory_movements",
        ["shipment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_rto_id"),
        "inventory_movements",
        ["rto_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_created_at"),
        "inventory_movements",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_inventory_movements_created_at"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_rto_id"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_shipment_id"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_order_id"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_movement_type"), table_name="inventory_movements")
    op.drop_index(
        op.f("ix_inventory_movements_product_variant_id"), table_name="inventory_movements"
    )
    op.drop_table("inventory_movements")
    sa.Enum(name="inventory_movement_type").drop(op.get_bind(), checkfirst=True)
    op.drop_column("product_variants", "available_quantity")
