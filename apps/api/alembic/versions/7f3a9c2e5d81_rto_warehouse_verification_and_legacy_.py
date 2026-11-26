"""RTO warehouse verification + legacy order reservation

Revision ID: 7f3a9c2e5d81
Revises: b4d8e2a6c1f7
Create Date: 2026-11-24 00:00:00.000000

Module 1 — completes the remaining Module 1 requirements:

1. `rtos`: adds warehouse-verification columns (`restock_status`,
   `verified_at`, `verified_by_user_id`, `accepted_quantity`,
   `rejected_quantity`). `restock_status` defaults `not_applicable` for
   every existing row -- no historical RTO is retroactively flagged
   AWAITING_VERIFICATION; only a FUTURE courier RECEIVED status (handled
   by `RTOService._apply_status_change`, going forward) sets it.
2. `inventory_movements`: replaces the single 3-column unique constraint
   `uq_inventory_movements_variant_order_type` with two PARTIAL unique
   indexes -- DISPATCH idempotency unchanged (same columns, now scoped
   to DISPATCH rows only), RTO_RESTOCK idempotency now keyed by `rto_id`
   (receipt-level) instead of `order_id`, so a second shipment/RTO on
   the same order+variant can restock its own receipt. See
   `app.models.inventory.InventoryMovement`'s module docstring.
3. `order_inventory_reservations`: new table, FK'd directly to the
   ACTIVE legacy `orders`/`product_variants` (never the dormant
   `order_workflow`/API-first architecture) -- the legacy order-placement
   "soft hold" reservation ledger. See `app.models.order_reservation`.

Additive except for #2's constraint swap; no existing row in any table
is deleted or rewritten. Postgres is the only deployment target for the
partial-index DDL below (SQLite's equivalent partial index is created
automatically by SQLAlchemy's `Index(..., sqlite_where=...)` at
`Base.metadata.create_all()` time for the test suite -- this migration
only needs to express it for Postgres).
"""

from __future__ import annotations

import app.db.base
import sqlalchemy as sa
from alembic import op

revision = "7f3a9c2e5d81"
down_revision = "b4d8e2a6c1f7"
branch_labels = None
depends_on = None

_RESTOCK_STATUS_ENUM_NAME = "rto_restock_status"


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
    restock_status_enum = sa.Enum(
        "not_applicable",
        "awaiting_verification",
        "verified",
        "rejected",
        name=_RESTOCK_STATUS_ENUM_NAME,
    )
    restock_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "rtos",
        sa.Column(
            "restock_status",
            restock_status_enum,
            server_default="not_applicable",
            nullable=False,
        ),
    )
    op.add_column(
        "rtos", sa.Column("verified_at", app.db.base.AwareDateTime(), nullable=True)
    )
    op.add_column(
        "rtos", sa.Column("verified_by_user_id", app.db.base.GUID(), nullable=True)
    )
    op.add_column("rtos", sa.Column("accepted_quantity", sa.Integer(), nullable=True))
    op.add_column("rtos", sa.Column("rejected_quantity", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_rtos_verified_by_user_id",
        "rtos",
        "users",
        ["verified_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_rtos_restock_status", "rtos", ["restock_status"])

    # Replace the single 3-column unique constraint with two partial
    # unique indexes -- see this module's docstring, point 2.
    op.drop_constraint(
        "uq_inventory_movements_variant_order_type", "inventory_movements", type_="unique"
    )
    op.create_index(
        "uq_inventory_movements_dispatch_variant_order",
        "inventory_movements",
        ["product_variant_id", "order_id"],
        unique=True,
        postgresql_where=sa.text("movement_type = 'dispatch'"),
    )
    op.create_index(
        "uq_inventory_movements_rto_restock_variant_rto",
        "inventory_movements",
        ["product_variant_id", "rto_id"],
        unique=True,
        postgresql_where=sa.text("movement_type = 'rto_restock'"),
    )

    op.create_table(
        "order_inventory_reservations",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("product_variant_id", app.db.base.GUID(), nullable=False),
        sa.Column("order_id", app.db.base.GUID(), nullable=False),
        sa.Column("warehouse_id", app.db.base.GUID(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "quantity > 0", name="ck_order_inventory_reservations_quantity_positive"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name="ck_order_inventory_reservations_status",
        ),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["product_variants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_variant_id",
            "order_id",
            name="uq_order_inventory_reservations_variant_order",
        ),
    )
    op.create_index(
        "ix_order_inventory_reservations_order_id", "order_inventory_reservations", ["order_id"]
    )
    op.create_index(
        "ix_order_inventory_reservations_variant_status",
        "order_inventory_reservations",
        ["product_variant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_order_inventory_reservations_variant_status",
        table_name="order_inventory_reservations",
    )
    op.drop_index(
        "ix_order_inventory_reservations_order_id", table_name="order_inventory_reservations"
    )
    op.drop_table("order_inventory_reservations")

    op.drop_index(
        "uq_inventory_movements_rto_restock_variant_rto", table_name="inventory_movements"
    )
    op.drop_index(
        "uq_inventory_movements_dispatch_variant_order", table_name="inventory_movements"
    )
    op.create_unique_constraint(
        "uq_inventory_movements_variant_order_type",
        "inventory_movements",
        ["product_variant_id", "order_id", "movement_type"],
    )

    op.drop_index("ix_rtos_restock_status", table_name="rtos")
    op.drop_constraint("fk_rtos_verified_by_user_id", "rtos", type_="foreignkey")
    op.drop_column("rtos", "rejected_quantity")
    op.drop_column("rtos", "accepted_quantity")
    op.drop_column("rtos", "verified_by_user_id")
    op.drop_column("rtos", "verified_at")
    op.drop_column("rtos", "restock_status")
    sa.Enum(name=_RESTOCK_STATUS_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
