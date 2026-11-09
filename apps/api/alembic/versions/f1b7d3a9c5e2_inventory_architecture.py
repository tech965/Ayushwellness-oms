"""inventory architecture: warehouses, provider variant mappings, reservations

Revision ID: f1b7d3a9c5e2
Revises: e8a3c1f7b5d2
Create Date: 2026-11-09 00:00:00.000000

Additive. No existing inventory row is changed or removed:

* `warehouses`: one default warehouse (`MAIN`) is inserted.
* `provider_variant_mappings`: (provider, external_variant_id) -> internal
  `product_variants.id`, backfilled for Shopify from the identifiers already
  on `product_variants` / `products`. Identifiers only -- no catalogue data.
  Unique on (provider, external_variant_id) and on (provider, variant_id).
* `inventory_reservations`: stock set aside per (variant, order); unique so
  a duplicate reserve can never double-hold; quantity > 0; status checked.
* `inventory_movements.warehouse_id`: nullable FK, backfilled to the default
  warehouse for every historical movement (history is otherwise untouched).

Postgres is the only deployment target; CHECK constraints are portable.
Downgrade drops the new objects and the new column; historical movements
are unchanged by either direction.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "f1b7d3a9c5e2"
down_revision = "e8a3c1f7b5d2"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "warehouses",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.execute(
        "INSERT INTO warehouses (id, code, name, is_default, is_active) "
        "VALUES (gen_random_uuid(), 'MAIN', 'Main warehouse', true, true)"
    )

    op.create_table(
        "provider_variant_mappings",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("external_product_id", sa.String(length=255), nullable=True),
        sa.Column("external_variant_id", sa.String(length=255), nullable=False),
        sa.Column("variant_id", app.db.base.GUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "external_variant_id", name="uq_provider_variant_mappings_external"
        ),
        sa.UniqueConstraint("provider", "variant_id", name="uq_provider_variant_mappings_variant"),
    )
    op.create_index(
        "ix_provider_variant_mappings_variant_id", "provider_variant_mappings", ["variant_id"]
    )
    # Identifiers only. DISTINCT ON guards the (rare) case of two internal
    # variants carrying the same Shopify id: the oldest keeps the mapping and
    # the other is left unmapped (reported by the mapping API), never merged.
    op.execute(
        """
        INSERT INTO provider_variant_mappings
            (id, provider, external_product_id, external_variant_id, variant_id)
        SELECT gen_random_uuid(), 'shopify', p.shopify_product_id, v.shopify_variant_id, v.id
        FROM (
            SELECT DISTINCT ON (shopify_variant_id) id, product_id, shopify_variant_id
            FROM product_variants
            WHERE shopify_variant_id IS NOT NULL
            ORDER BY shopify_variant_id, created_at, id
        ) v
        JOIN products p ON p.id = v.product_id
        ON CONFLICT DO NOTHING
        """
    )

    op.create_table(
        "inventory_reservations",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("variant_id", app.db.base.GUID(), nullable=False),
        sa.Column("order_id", app.db.base.GUID(), nullable=False),
        sa.Column("warehouse_id", app.db.base.GUID(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("quantity > 0", name="ck_inventory_reservations_quantity_positive"),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'consumed')", name="ck_inventory_reservations_status"
        ),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["order_workflow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "variant_id", "order_id", name="uq_inventory_reservations_variant_order"
        ),
    )
    op.create_index("ix_inventory_reservations_order_id", "inventory_reservations", ["order_id"])
    op.create_index(
        "ix_inventory_reservations_variant_status",
        "inventory_reservations",
        ["variant_id", "status"],
    )

    op.add_column(
        "inventory_movements", sa.Column("warehouse_id", app.db.base.GUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_inventory_movements_warehouse_id",
        "inventory_movements",
        "warehouses",
        ["warehouse_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_inventory_movements_warehouse_id", "inventory_movements", ["warehouse_id"])
    op.execute(
        "UPDATE inventory_movements SET warehouse_id = "
        "(SELECT id FROM warehouses WHERE is_default LIMIT 1) WHERE warehouse_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_movements_warehouse_id", table_name="inventory_movements")
    op.drop_constraint(
        "fk_inventory_movements_warehouse_id", "inventory_movements", type_="foreignkey"
    )
    op.drop_column("inventory_movements", "warehouse_id")
    op.drop_index("ix_inventory_reservations_variant_status", table_name="inventory_reservations")
    op.drop_index("ix_inventory_reservations_order_id", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
    op.drop_index("ix_provider_variant_mappings_variant_id", table_name="provider_variant_mappings")
    op.drop_table("provider_variant_mappings")
    op.drop_table("warehouses")
