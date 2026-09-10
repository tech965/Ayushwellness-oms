"""order shipping address shopify sync state

Revision ID: a8c3f1d5e7b2
Revises: f4a2c6e9b1d7
Create Date: 2026-09-10 09:15:00.000000

Adds three columns to `orders`, backing the outbound OMS -> Shopify push
for a Telecaller-edited `shipping_address` (`ShopifyFulfillmentService.
sync_shipping_address`). Reuses the `shopify_sync_status` enum type
already created by `f6b2c9a3d7e1` (`create_type=False` -- one shared
Postgres type per `app.models.enums.sa_enum`, not a second `CREATE TYPE`).

`shipping_address_sync_status` is NOT NULL but backward compatible:
defaults to 'not_applicable' at both the Python/ORM layer and the
database layer (`server_default=`), so every existing row is backfilled
automatically at ALTER TABLE time -- no separate data migration, no
downtime.

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
revision: str = "a8c3f1d5e7b2"
down_revision: str | None = "f4a2c6e9b1d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "shipping_address_sync_status",
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
        sa.Column("shipping_address_sync_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("shipping_address_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "shipping_address_synced_at")
    op.drop_column("orders", "shipping_address_sync_error")
    op.drop_column("orders", "shipping_address_sync_status")
