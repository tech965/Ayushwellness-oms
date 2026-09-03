"""orders order_datetime index

Revision ID: f3a91c7d5e02
Revises: e7f4a2c9d1b6
Create Date: 2026-09-03 00:00:00.000000

Confirmed live (EXPLAIN ANALYZE against production, 48k orders): every
analytics/dashboard query filters `orders.order_datetime` (`resolve_range`
in app/services/analytics_service.py), and this column had no index --
forcing a sequential scan (~700ms per query, and the dashboard fires
roughly eight such queries per navigation) directly responsible for the
reported multi-second dashboard load delay. Additive only -- no data
change, no column type change, no other index touched.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a91c7d5e02"
down_revision: str | None = "e7f4a2c9d1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_orders_order_datetime", "orders", ["order_datetime"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_orders_order_datetime", table_name="orders")
