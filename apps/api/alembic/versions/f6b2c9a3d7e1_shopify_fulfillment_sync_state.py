"""shopify fulfillment sync state

Revision ID: f6b2c9a3d7e1
Revises: d4e5f6a7b8c9
Create Date: 2026-09-10 00:00:00.000000

Additive only -- four new nullable/defaulted columns on the existing
`shipments` table, backing the OMS -> Shopify outbound fulfillment push
(Phase 6). `shopify_sync_status` is a new NOT NULL enum column but is
fully backward compatible: it defaults to 'not_applicable' both at the
Python/ORM layer (`default=`) and at the database layer
(`server_default=`), so every existing row is backfilled automatically by
Postgres at ALTER TABLE time -- no separate data migration/backfill step,
no downtime.

No existing column changes type or is dropped. Nothing in application
code reads these columns before this migration is applied (see
`app/services/shopify_fulfillment_service.py`), matching the "don't
repeat the missing-migration 500" lesson from a prior incident.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f6b2c9a3d7e1"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(
        "not_applicable", "pending", "synced", "failed", name="shopify_sync_status"
    ).create(bind, checkfirst=True)

    op.add_column(
        "shipments",
        sa.Column("shopify_fulfillment_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_shipments_shopify_fulfillment_id",
        "shipments",
        ["shopify_fulfillment_id"],
        unique=True,
    )
    op.add_column(
        "shipments",
        sa.Column(
            "shopify_sync_status",
            postgresql.ENUM(
                "not_applicable",
                "pending",
                "synced",
                "failed",
                name="shopify_sync_status",
                create_type=False,
            ),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        "shipments",
        sa.Column("shopify_sync_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "shipments",
        sa.Column("shopify_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shipments", "shopify_synced_at")
    op.drop_column("shipments", "shopify_sync_error")
    op.drop_column("shipments", "shopify_sync_status")
    op.drop_index("ix_shipments_shopify_fulfillment_id", table_name="shipments")
    op.drop_column("shipments", "shopify_fulfillment_id")
    postgresql.ENUM(name="shopify_sync_status").drop(op.get_bind(), checkfirst=True)
