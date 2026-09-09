"""order confirmation shopify fulfillment sync state

Revision ID: b3e7a1c9f4d2
Revises: c2a9f4e1b8d7
Create Date: 2026-09-09 14:00:00.000000

Adds four columns to `orders`, backing the outbound OMS -> Shopify
fulfillment push for Telecaller confirmation (distinct from the existing
per-`Shipment` push added by `f6b2c9a3d7e1` for real shipping/AWB): a real
Shopify `Fulfillment` is created once an order is confirmed (no AWB, no
Shiprocket shipment), and `unconfirm_order` reverses exactly that
Fulfillment via its own id -- never a fulfillment created independently
outside the OMS.

Reuses the `shopify_sync_status` enum type `f6b2c9a3d7e1` already created
(`create_type=False` -- one shared Postgres type per `app.models.enums.
sa_enum`, not a second `CREATE TYPE`). `shopify_confirmation_sync_status`
is NOT NULL but backward compatible: defaults to 'not_applicable' at both
the Python/ORM layer and the database layer (`server_default=`), so every
existing row is backfilled automatically at ALTER TABLE time -- no
separate data migration, no downtime.

Additive and nullable/defaulted only. No existing column changes type or
is dropped, and nothing in application code reads these columns before
this migration is applied.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b3e7a1c9f4d2"
down_revision: str | None = "c2a9f4e1b8d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("shopify_confirmation_fulfillment_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_orders_shopify_confirmation_fulfillment_id",
        "orders",
        ["shopify_confirmation_fulfillment_id"],
        unique=True,
    )
    op.add_column(
        "orders",
        sa.Column(
            "shopify_confirmation_sync_status",
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
        "orders",
        sa.Column("shopify_confirmation_sync_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("shopify_confirmation_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "shopify_confirmation_synced_at")
    op.drop_column("orders", "shopify_confirmation_sync_error")
    op.drop_column("orders", "shopify_confirmation_sync_status")
    op.drop_index("ix_orders_shopify_confirmation_fulfillment_id", table_name="orders")
    op.drop_column("orders", "shopify_confirmation_fulfillment_id")
