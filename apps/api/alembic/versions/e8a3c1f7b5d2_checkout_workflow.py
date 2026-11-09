"""checkout_workflow: OMS-owned abandoned-checkout reference

Revision ID: e8a3c1f7b5d2
Revises: d6e2b9a4c7f1
Create Date: 2026-11-08 00:00:00.000000

Additive. Creates `checkout_workflow` (reference + OMS notes/conversion
bookkeeping, no customer or checkout data), backfills one row per existing
`abandoned_checkouts` row REUSING its id, then re-points
`checkout_assignments.checkout_id` and `checkout_call_attempts.checkout_id`
from `abandoned_checkouts.id` to `checkout_workflow.id`. Existing values are
unchanged, so every historical assignment and call attempt keeps matching.
Aborts (transactionally) if any checkout is left without its workflow row.
Nothing is deleted; `abandoned_checkouts` is untouched.

Postgres-only. Downgrade restores the original FKs as NOT VALID and drops
`checkout_workflow`.
"""

from __future__ import annotations

import app.db.base
import sqlalchemy as sa
from alembic import op

revision = "e8a3c1f7b5d2"
down_revision = "d6e2b9a4c7f1"
branch_labels = None
depends_on = None

_TABLES = ("checkout_assignments", "checkout_call_attempts")

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
          AND rel.relname = '{table}'
          AND ref.relname = 'abandoned_checkouts'
          AND att.attname = 'checkout_id'
    LOOP
        EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', fk_name);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "checkout_workflow",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("source_system", sa.String(length=50), server_default="shopify", nullable=False),
        sa.Column("external_checkout_id", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("converted_at", app.db.base.AwareDateTime(), nullable=True),
        sa.Column("converted_order_ref", app.db.base.GUID(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checkout_workflow_external_checkout_id",
        "checkout_workflow",
        ["external_checkout_id"],
        unique=True,
    )

    op.execute(
        """
        INSERT INTO checkout_workflow (
            id, source_system, external_checkout_id, created_at, updated_at
        )
        SELECT
            c.id, COALESCE(c.source_system, 'manual'),
            COALESCE(c.shopify_checkout_id, c.external_id, 'local:' || c.id::text),
            now(), now()
        FROM abandoned_checkouts c
        WHERE NOT EXISTS (SELECT 1 FROM checkout_workflow w WHERE w.id = c.id)
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM abandoned_checkouts c
                WHERE NOT EXISTS (SELECT 1 FROM checkout_workflow w WHERE w.id = c.id)
            ) THEN
                RAISE EXCEPTION 'checkout_workflow backfill incomplete';
            END IF;
        END $$;
        """
    )

    for table in _TABLES:
        op.execute(_DROP_FK.format(table=table))
        op.create_foreign_key(
            f"fk_{table}_checkout_id_checkout_workflow",
            table,
            "checkout_workflow",
            ["checkout_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_constraint(f"fk_{table}_checkout_id_checkout_workflow", table, type_="foreignkey")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_checkout_id_fkey")
        op.create_foreign_key(
            f"{table}_checkout_id_fkey",
            table,
            "abandoned_checkouts",
            ["checkout_id"],
            ["id"],
            ondelete="CASCADE",
            postgresql_not_valid=True,
        )
    op.drop_index("ix_checkout_workflow_external_checkout_id", table_name="checkout_workflow")
    op.drop_table("checkout_workflow")
