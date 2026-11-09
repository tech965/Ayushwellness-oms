"""order_workflow: OMS-owned order state keyed by external order id

Revision ID: a9d3e5b7c1f4
Revises: f3a7c9e1b6d2
Create Date: 2026-11-08 00:00:00.000000

API-first architecture groundwork (Shopify stays the source of truth for
order content). Additive and non-destructive:

1. Creates `order_workflow` -- ONLY OMS-owned state (status, internal
   notes, telecaller confirmation, confirmation-tag sync bookkeeping),
   keyed by `external_order_id`. No Shopify field is copied.
2. Backfills one `order_workflow` row per existing Shopify `orders` row,
   REUSING `orders.id` as `order_workflow.id`, so every existing
   `order_assignments.order_id` (and any call-log/activity reference)
   keeps pointing at the same uuid. Nothing in `orders` is modified or
   deleted. Re-running the INSERT is safe (`ON CONFLICT DO NOTHING`).
3. Drops ONLY the foreign-key constraint from `order_assignments.order_id`
   to `orders.id` (the column, its index and every row are untouched), so
   an assignment can reference an order that exists only in Shopify.

Postgres-only (the project's sole deployment target; the test suite builds
its schema from the model code on SQLite and never runs this file).

Downgrade: drops `order_workflow` (this loses any workflow rows created
for live-only orders -- take a backup first) and re-adds the FK as
NOT VALID so it succeeds even if live-only assignments exist; those rows
are not deleted.
"""

from __future__ import annotations

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9d3e5b7c1f4"
down_revision = "f3a7c9e1b6d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_workflow",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("source_system", sa.String(length=50), server_default="shopify", nullable=False),
        sa.Column("external_order_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="order_status", create_type=False),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("confirmed_by_telecaller_id", app.db.base.GUID(), nullable=True),
        sa.Column("confirmed_at", app.db.base.AwareDateTime(), nullable=True),
        sa.Column(
            "shopify_confirmation_sync_status",
            postgresql.ENUM(name="shopify_sync_status", create_type=False),
            nullable=False,
        ),
        sa.Column("shopify_confirmation_sync_error", sa.Text(), nullable=True),
        sa.Column("shopify_confirmation_synced_at", app.db.base.AwareDateTime(), nullable=True),
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
        sa.ForeignKeyConstraint(["confirmed_by_telecaller_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_order_workflow_external_order_id"),
        "order_workflow",
        ["external_order_id"],
        unique=True,
    )
    op.create_index(op.f("ix_order_workflow_status"), "order_workflow", ["status"], unique=False)
    op.create_index(
        op.f("ix_order_workflow_confirmed_by_telecaller_id"),
        "order_workflow",
        ["confirmed_by_telecaller_id"],
        unique=False,
    )

    # Backfill OMS-owned state only. `id` = the legacy orders.id.
    op.execute(
        """
        INSERT INTO order_workflow (
            id, source_system, external_order_id, status, notes,
            confirmed_by_telecaller_id, confirmed_at,
            shopify_confirmation_sync_status, shopify_confirmation_sync_error,
            shopify_confirmation_synced_at, created_at, updated_at
        )
        SELECT
            id, 'shopify', COALESCE(shopify_order_id, external_id), status, notes,
            confirmed_by_telecaller_id, confirmed_at,
            shopify_confirmation_sync_status, shopify_confirmation_sync_error,
            shopify_confirmation_synced_at, now(), now()
        FROM orders
        WHERE COALESCE(shopify_order_id, external_id) IS NOT NULL
          AND (source_system = 'shopify' OR shopify_order_id IS NOT NULL)
        ON CONFLICT DO NOTHING
        """
    )

    # Drop only the order_assignments.order_id -> orders.id FK, found by
    # catalog lookup (the constraint name was auto-generated). Works in
    # both online and `--sql` offline mode.
    op.execute(
        """
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
                  AND rel.relname = 'order_assignments'
                  AND ref.relname = 'orders'
                  AND att.attname = 'order_id'
            LOOP
                EXECUTE format('ALTER TABLE order_assignments DROP CONSTRAINT %I', fk_name);
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    # `b4f8a2c6d1e9`'s downgrade may already have restored this constraint.
    op.execute(
        "ALTER TABLE order_assignments DROP CONSTRAINT IF EXISTS order_assignments_order_id_fkey"
    )
    op.create_foreign_key(
        "order_assignments_order_id_fkey",
        "order_assignments",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.drop_index(op.f("ix_order_workflow_confirmed_by_telecaller_id"), table_name="order_workflow")
    op.drop_index(op.f("ix_order_workflow_status"), table_name="order_workflow")
    op.drop_index(op.f("ix_order_workflow_external_order_id"), table_name="order_workflow")
    op.drop_table("order_workflow")
