"""inventory management system (IMS) tables

Revision ID: a1b2c3d4e5f6
Revises: f1b7d3a9c5e2
Create Date: 2026-11-15 00:00:00.000000

Additive only. Nothing existing is altered except four new, defaulted
columns on `warehouses` (address, description, sort_order, is_ims).

* Four IMS warehouse slots are inserted (configuration, not stock):
  Royal Palm 251, Royal Palm 310, Aayush Wellness Ltd. Office 402, and an
  unconfigured, inactive "Warehouse 4" slot whose name/code staff set later.
* No inventory quantity is created. Opening balances are loaded through the
  IMS opening-balance endpoint once verified.
* `ims_ledger` / `ims_adjustments` are append-only: a PostgreSQL trigger
  rejects UPDATE and DELETE.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.db.base

revision = "a1b2c3d4e5f6"
down_revision = "f1b7d3a9c5e2"
branch_labels = None
depends_on = None

_UNITS = "('BOX','OUTER','SACHETS','ZIPPER','BOTTLE','JAR')"


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", app.db.base.AwareDateTime(), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", app.db.base.AwareDateTime(), server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", app.db.base.AwareDateTime(), server_default=sa.text("now()"), nullable=False
    )


def _id() -> sa.Column:
    return sa.Column("id", app.db.base.GUID(), nullable=False)


def upgrade() -> None:
    op.add_column("warehouses", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("warehouses", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "warehouses",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "warehouses",
        sa.Column("is_ims", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "ims_packaging_products",
        _id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(255), nullable=True),
        sa.Column("shopify_product_id", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "ims_items",
        _id(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("sku", sa.String(255), nullable=True),
        sa.Column("category", sa.String(255), nullable=True),
        sa.Column("shopify_product_id", sa.String(255), nullable=True),
        sa.Column("shopify_variant_id", sa.String(255), nullable=True),
        sa.Column("packaging_product_id", app.db.base.GUID(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("low_stock_threshold", sa.BigInteger(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["packaging_product_id"], ["ims_packaging_products.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shopify_variant_id", name="uq_ims_items_shopify_variant"),
        sa.UniqueConstraint("packaging_product_id", "unit", name="uq_ims_items_pkg_unit"),
        sa.CheckConstraint("kind IN ('finished', 'packaging')", name="ck_ims_items_kind"),
        sa.CheckConstraint(
            "(kind = 'finished' AND shopify_variant_id IS NOT NULL AND unit IS NULL "
            "AND packaging_product_id IS NULL) OR "
            "(kind = 'packaging' AND packaging_product_id IS NOT NULL AND unit IS NOT NULL "
            "AND shopify_variant_id IS NULL)",
            name="ck_ims_items_shape",
        ),
        sa.CheckConstraint(f"unit IS NULL OR unit IN {_UNITS}", name="ck_ims_items_unit"),
    )
    op.create_index("ix_ims_items_kind", "ims_items", ["kind"])
    op.create_index("ix_ims_items_sku", "ims_items", ["sku"])
    op.create_index("ix_ims_items_category", "ims_items", ["category"])
    op.create_index("ix_ims_items_packaging_product_id", "ims_items", ["packaging_product_id"])

    op.create_table(
        "ims_unit_conversions",
        _id(),
        sa.Column("packaging_product_id", app.db.base.GUID(), nullable=False),
        sa.Column("larger_unit", sa.String(20), nullable=False),
        sa.Column("smaller_unit", sa.String(20), nullable=False),
        sa.Column("ratio", sa.Integer(), nullable=False),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["packaging_product_id"], ["ims_packaging_products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "packaging_product_id", "larger_unit", "smaller_unit", name="uq_ims_conversion_pair"
        ),
        sa.CheckConstraint("ratio > 0", name="ck_ims_conversion_ratio_positive"),
        sa.CheckConstraint("larger_unit <> smaller_unit", name="ck_ims_conversion_distinct"),
        sa.CheckConstraint(
            f"larger_unit IN {_UNITS} AND smaller_unit IN {_UNITS}", name="ck_ims_conversion_units"
        ),
    )
    op.create_index(
        "ix_ims_unit_conversions_packaging_product_id",
        "ims_unit_conversions",
        ["packaging_product_id"],
    )

    op.create_table(
        "ims_balances",
        _id(),
        sa.Column("warehouse_id", app.db.base.GUID(), nullable=False),
        sa.Column("item_id", app.db.base.GUID(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", app.db.base.AwareDateTime(), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["item_id"], ["ims_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("warehouse_id", "item_id", name="uq_ims_balances_wh_item"),
        sa.CheckConstraint("quantity >= 0", name="ck_ims_balances_non_negative"),
    )
    op.create_index("ix_ims_balances_warehouse_id", "ims_balances", ["warehouse_id"])
    op.create_index("ix_ims_balances_item_id", "ims_balances", ["item_id"])

    op.create_table(
        "ims_external_parties",
        _id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "ims_transfers",
        _id(),
        sa.Column("reference_no", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("source_warehouse_id", app.db.base.GUID(), nullable=False),
        sa.Column("destination_warehouse_id", app.db.base.GUID(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", app.db.base.GUID(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["source_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["destination_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_no"),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint(
            "source_warehouse_id <> destination_warehouse_id", name="ck_ims_transfers_distinct"
        ),
    )
    op.create_index("ix_ims_transfers_created_at", "ims_transfers", ["created_at"])
    op.create_index("ix_ims_transfers_source_warehouse_id", "ims_transfers", ["source_warehouse_id"])
    op.create_index(
        "ix_ims_transfers_destination_warehouse_id", "ims_transfers", ["destination_warehouse_id"]
    )

    op.create_table(
        "ims_transfer_lines",
        _id(),
        sa.Column("transfer_id", app.db.base.GUID(), nullable=False),
        sa.Column("item_id", app.db.base.GUID(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["transfer_id"], ["ims_transfers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["item_id"], ["ims_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0", name="ck_ims_transfer_lines_qty"),
    )
    op.create_index("ix_ims_transfer_lines_transfer_id", "ims_transfer_lines", ["transfer_id"])
    op.create_index("ix_ims_transfer_lines_item_id", "ims_transfer_lines", ["item_id"])

    op.create_table(
        "ims_returns",
        _id(),
        sa.Column("reference_no", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("warehouse_id", app.db.base.GUID(), nullable=False),
        sa.Column("party_id", app.db.base.GUID(), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column("document_no", sa.String(120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", app.db.base.GUID(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["party_id"], ["ims_external_parties.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_no"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_ims_returns_return_date", "ims_returns", ["return_date"])
    op.create_index("ix_ims_returns_warehouse_id", "ims_returns", ["warehouse_id"])
    op.create_index("ix_ims_returns_party_id", "ims_returns", ["party_id"])

    op.create_table(
        "ims_return_lines",
        _id(),
        sa.Column("return_id", app.db.base.GUID(), nullable=False),
        sa.Column("item_id", app.db.base.GUID(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["return_id"], ["ims_returns.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["item_id"], ["ims_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0", name="ck_ims_return_lines_qty"),
    )
    op.create_index("ix_ims_return_lines_return_id", "ims_return_lines", ["return_id"])
    op.create_index("ix_ims_return_lines_item_id", "ims_return_lines", ["item_id"])

    op.create_table(
        "ims_adjustments",
        _id(),
        sa.Column("reference_no", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("warehouse_id", app.db.base.GUID(), nullable=False),
        sa.Column("item_id", app.db.base.GUID(), nullable=False),
        sa.Column("adjustment_type", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", app.db.base.GUID(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["item_id"], ["ims_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_no"),
        sa.UniqueConstraint("idempotency_key"),
        sa.CheckConstraint("quantity > 0", name="ck_ims_adjustments_qty"),
        sa.CheckConstraint(
            "adjustment_type IN ('increase','decrease','damaged','missing',"
            "'count_correction','other')",
            name="ck_ims_adjustments_type",
        ),
        sa.CheckConstraint("direction IN ('in','out')", name="ck_ims_adjustments_direction"),
        sa.CheckConstraint("length(reason) > 0", name="ck_ims_adjustments_reason"),
    )
    op.create_index("ix_ims_adjustments_warehouse_id", "ims_adjustments", ["warehouse_id"])
    op.create_index("ix_ims_adjustments_item_id", "ims_adjustments", ["item_id"])
    op.create_index("ix_ims_adjustments_created_at", "ims_adjustments", ["created_at"])

    op.create_table(
        "ims_ledger",
        _id(),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("warehouse_id", app.db.base.GUID(), nullable=False),
        sa.Column("item_id", app.db.base.GUID(), nullable=False),
        sa.Column("quantity_delta", sa.BigInteger(), nullable=False),
        sa.Column("quantity_before", sa.BigInteger(), nullable=False),
        sa.Column("quantity_after", sa.BigInteger(), nullable=False),
        sa.Column("reference_no", sa.String(40), nullable=True),
        sa.Column("transfer_id", app.db.base.GUID(), nullable=True),
        sa.Column("return_id", app.db.base.GUID(), nullable=True),
        sa.Column("adjustment_id", app.db.base.GUID(), nullable=True),
        sa.Column("actor_user_id", app.db.base.GUID(), nullable=True),
        sa.Column("occurred_at", app.db.base.AwareDateTime(), nullable=False),
        _created_at(),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["item_id"], ["ims_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transfer_id"], ["ims_transfers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["return_id"], ["ims_returns.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["adjustment_id"], ["ims_adjustments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "movement_type IN ('opening_balance','inward','outward','transfer_out',"
            "'transfer_in','return_in','adjustment_in','adjustment_out','damaged','missing')",
            name="ck_ims_ledger_type",
        ),
        sa.CheckConstraint("quantity_delta <> 0", name="ck_ims_ledger_delta_nonzero"),
        sa.CheckConstraint("quantity_after >= 0", name="ck_ims_ledger_after_non_negative"),
        sa.CheckConstraint(
            "quantity_after = quantity_before + quantity_delta", name="ck_ims_ledger_arithmetic"
        ),
    )
    op.create_index("ix_ims_ledger_movement_type", "ims_ledger", ["movement_type"])
    op.create_index(
        "ix_ims_ledger_wh_item_occurred", "ims_ledger", ["warehouse_id", "item_id", "occurred_at"]
    )
    op.create_index("ix_ims_ledger_occurred", "ims_ledger", ["occurred_at"])
    op.create_index("ix_ims_ledger_reference", "ims_ledger", ["reference_no"])
    op.create_index("ix_ims_ledger_transfer_id", "ims_ledger", ["transfer_id"])
    op.create_index("ix_ims_ledger_return_id", "ims_ledger", ["return_id"])
    op.create_index("ix_ims_ledger_adjustment_id", "ims_ledger", ["adjustment_id"])

    # Append-only enforcement at the database level.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ims_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; record a new adjustment instead', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("ims_ledger", "ims_adjustments"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ims_reject_mutation()"
        )

    # Warehouse slots (configuration only; no quantities).
    for code, name, order, active, address in (
        ("251", "Royal Palm", 1, True, None),
        ("310", "Royal Palm", 2, True, None),
        ("402", "Aayush Wellness Ltd. Office", 3, True, None),
        ("SLOT-4", "Warehouse 4 (not configured)", 4, False, None),
    ):
        op.execute(
            sa.text(
                "INSERT INTO warehouses (id, code, name, is_default, is_active, sort_order, "
                "is_ims, address) VALUES (gen_random_uuid(), :code, :name, false, :active, "
                ":order, true, :address) ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=name, active=active, order=order, address=address)
        )


def downgrade() -> None:
    for table in ("ims_ledger", "ims_adjustments"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS ims_reject_mutation()")
    for table in (
        "ims_ledger",
        "ims_adjustments",
        "ims_return_lines",
        "ims_returns",
        "ims_transfer_lines",
        "ims_transfers",
        "ims_external_parties",
        "ims_balances",
        "ims_unit_conversions",
        "ims_items",
        "ims_packaging_products",
    ):
        op.drop_table(table)
    op.execute("DELETE FROM warehouses WHERE is_ims = true")
    for col in ("is_ims", "sort_order", "description", "address"):
        op.drop_column("warehouses", col)
