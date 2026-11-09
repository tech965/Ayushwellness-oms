"""shipment_refs: identifier-only Shiprocket references

Revision ID: d6e2b9a4c7f1
Revises: c5a1d7e3f9b2
Create Date: 2026-11-08 00:00:00.000000

Additive. Creates `shipment_refs` (identifiers + OMS bookkeeping only, no
status/tracking/payload), backfills one row per existing `shipments` row
REUSING `shipments.id`, then re-points the inventory ledger's links
(`inventory_movements.order_id` -> `order_workflow.id`,
`inventory_movements.shipment_id` -> `shipment_refs.id`) so stock movements
work for live-only orders and keep matching every historical row (the ids
are unchanged). `inventory_movements.rto_id` keeps its FK to `rtos`
(historical only). No row is deleted or rewritten; the migration aborts if
any referenced row is missing its replacement.

Postgres-only. Downgrade restores the original FKs as NOT VALID and drops
`shipment_refs`.
"""

from __future__ import annotations

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d6e2b9a4c7f1"
down_revision = "c5a1d7e3f9b2"
branch_labels = None
depends_on = None

_DROP_FK = """
DO $$
DECLARE fk_name text;
BEGIN
    FOR fk_name IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_class ref ON ref.oid = con.confrelid
        JOIN pg_attribute att
          ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
        WHERE con.contype = 'f'
          AND rel.relname = 'inventory_movements'
          AND ref.relname = '{referred}'
          AND att.attname = '{column}'
    LOOP
        EXECUTE format('ALTER TABLE inventory_movements DROP CONSTRAINT %I', fk_name);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "shipment_refs",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("order_id", app.db.base.GUID(), nullable=False),
        sa.Column("shiprocket_order_id", sa.String(length=64), nullable=True),
        sa.Column("shiprocket_shipment_id", sa.String(length=64), nullable=True),
        sa.Column("awb", sa.String(length=64), nullable=True),
        sa.Column("cancelled_at", app.db.base.AwareDateTime(), nullable=True),
        sa.Column("shopify_fulfillment_id", sa.String(length=64), nullable=True),
        sa.Column(
            "shopify_sync_status",
            postgresql.ENUM(name="shopify_sync_status", create_type=False),
            nullable=False,
        ),
        sa.Column("shopify_sync_error", sa.Text(), nullable=True),
        sa.Column("shopify_synced_at", app.db.base.AwareDateTime(), nullable=True),
        sa.Column("created_by", app.db.base.GUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["order_id"], ["order_workflow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shiprocket_shipment_id"),
    )
    op.create_index("ix_shipment_refs_order_id", "shipment_refs", ["order_id"])
    op.create_index(
        "ix_shipment_refs_shiprocket_order_id", "shipment_refs", ["shiprocket_order_id"]
    )
    op.create_index("ix_shipment_refs_awb", "shipment_refs", ["awb"])

    # Identifiers only: no status, tracking, courier or payload is copied.
    op.execute(
        """
        INSERT INTO shipment_refs (
            id, order_id, shiprocket_order_id, shiprocket_shipment_id, awb,
            shopify_fulfillment_id, shopify_sync_status, shopify_sync_error,
            shopify_synced_at, created_at, updated_at
        )
        SELECT
            s.id, s.order_id, NULLIF(s.raw_external_payload ->> 'order_id', ''),
            s.shiprocket_shipment_id, s.awb, s.shopify_fulfillment_id,
            s.shopify_sync_status, s.shopify_sync_error, s.shopify_synced_at,
            s.created_at, s.updated_at
        FROM shipments s
        WHERE NOT EXISTS (SELECT 1 FROM shipment_refs r WHERE r.id = s.id)
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM shipments s
                WHERE NOT EXISTS (SELECT 1 FROM shipment_refs r WHERE r.id = s.id)
            ) THEN
                RAISE EXCEPTION 'shipment_refs backfill incomplete';
            END IF;
        END $$;
        """
    )

    op.execute(_DROP_FK.format(referred="orders", column="order_id"))
    op.execute(_DROP_FK.format(referred="shipments", column="shipment_id"))
    op.create_foreign_key(
        "fk_inventory_movements_order_id_order_workflow",
        "inventory_movements",
        "order_workflow",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_inventory_movements_shipment_id_shipment_refs",
        "inventory_movements",
        "shipment_refs",
        ["shipment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_inventory_movements_shipment_id_shipment_refs",
        "inventory_movements",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_inventory_movements_order_id_order_workflow", "inventory_movements", type_="foreignkey"
    )
    op.execute(
        "ALTER TABLE inventory_movements DROP CONSTRAINT IF EXISTS inventory_movements_order_id_fkey"
    )
    op.execute(
        "ALTER TABLE inventory_movements "
        "DROP CONSTRAINT IF EXISTS inventory_movements_shipment_id_fkey"
    )
    op.create_foreign_key(
        "inventory_movements_order_id_fkey",
        "inventory_movements",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.create_foreign_key(
        "inventory_movements_shipment_id_fkey",
        "inventory_movements",
        "shipments",
        ["shipment_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    op.drop_index("ix_shipment_refs_awb", table_name="shipment_refs")
    op.drop_index("ix_shipment_refs_shiprocket_order_id", table_name="shipment_refs")
    op.drop_index("ix_shipment_refs_order_id", table_name="shipment_refs")
    op.drop_table("shipment_refs")
