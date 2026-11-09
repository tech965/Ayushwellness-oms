"""point assignments, call attempts and order events at order_workflow

Revision ID: b4f8a2c6d1e9
Revises: a9d3e5b7c1f4
Create Date: 2026-11-08 00:00:00.000000

Completes the move of internal order workflow data onto `order_workflow`
(created by `a9d3e5b7c1f4`) so it works for orders that exist only in
Shopify. Additive and non-destructive -- no row is deleted or rewritten:

1. Backfills an `order_workflow` row for EVERY remaining `orders` row
   (including manual / non-Shopify ones, keyed `local:<uuid>`), reusing
   `orders.id`, so each historical order has a workflow row with the same
   uuid. Provider + external id keying (`source_system`,
   `external_order_id`) also fits future marketplace orders.
2. Verifies that invariant; the migration ABORTS (transactional DDL) if
   any order is still missing its workflow row, rather than leaving
   dangling references.
3. Replaces the foreign keys `order_assignments.order_id`,
   `call_attempts.order_id` and `order_events.order_id` -> `orders.id`
   with foreign keys -> `order_workflow.id` (ON DELETE CASCADE), keeping
   referential integrity enforced by the database. Adding a validated FK
   scans each table once; if any existing row referenced a missing
   workflow row the migration fails and nothing is applied.

Postgres-only; never executed by the SQLite-based test suite.

Downgrade restores the original FKs to `orders.id` as NOT VALID (so rows
that reference live-only orders do not block it) and leaves
`order_workflow` in place.
"""

from __future__ import annotations

from alembic import op

revision = "b4f8a2c6d1e9"
down_revision = "a9d3e5b7c1f4"
branch_labels = None
depends_on = None

_TABLES = ("order_assignments", "call_attempts", "order_events")

_DROP_ORDERS_FK = """
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
          AND rel.relname = '{table}'
          AND ref.relname = 'orders'
          AND att.attname = 'order_id'
    LOOP
        EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', fk_name);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO order_workflow (
            id, source_system, external_order_id, status, notes,
            confirmed_by_telecaller_id, confirmed_at,
            shopify_confirmation_sync_status, shopify_confirmation_sync_error,
            shopify_confirmation_synced_at, created_at, updated_at
        )
        SELECT
            o.id, COALESCE(o.source_system, 'manual'),
            COALESCE(o.shopify_order_id, o.external_id, 'local:' || o.id::text),
            o.status, o.notes, o.confirmed_by_telecaller_id, o.confirmed_at,
            o.shopify_confirmation_sync_status, o.shopify_confirmation_sync_error,
            o.shopify_confirmation_synced_at, now(), now()
        FROM orders o
        WHERE NOT EXISTS (SELECT 1 FROM order_workflow w WHERE w.id = o.id)
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM orders o
                WHERE NOT EXISTS (SELECT 1 FROM order_workflow w WHERE w.id = o.id)
            ) THEN
                RAISE EXCEPTION
                    'order_workflow backfill incomplete: some orders have no workflow row';
            END IF;
        END $$;
        """
    )

    for table in _TABLES:
        op.execute(_DROP_ORDERS_FK.format(table=table))
        op.create_foreign_key(
            f"fk_{table}_order_id_order_workflow",
            table,
            "order_workflow",
            ["order_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"fk_{table}_order_id_order_workflow", table, type_="foreignkey")
        op.create_foreign_key(
            f"{table}_order_id_fkey",
            table,
            "orders",
            ["order_id"],
            ["id"],
            ondelete="CASCADE",
            postgresql_not_valid=True,
        )
