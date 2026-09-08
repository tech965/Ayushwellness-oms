"""add uniqueness constraint to inventory_movements (concurrency safety)

Revision ID: c9f4a2e6b813
Revises: b7d2e5a91c3f
Create Date: 2026-09-08 00:00:00.000000

Prevents two concurrent transactions from both recording a DISPATCH or
RTO_RESTOCK movement for the same (variant, order) -- the existing
`exists_for_order` check is only a snapshot-time read and is not by
itself safe against a race between, e.g., a webhook delivery and an
overlapping pull-sync re-scan for the same shipment. `order_id` is
always NULL for MANUAL_ADJUSTMENT/INITIAL_STOCK rows; NULL never
collides with itself in a SQL unique constraint, so this never restricts
those movement types (multiple manual adjustments for the same variant
remain unrestricted, as intended).

IMPORTANT before applying to any existing database: if duplicate
(product_variant_id, order_id, movement_type) rows already exist (e.g.
from before this fix), this migration's ADD CONSTRAINT will fail. Check
first:

    SELECT product_variant_id, order_id, movement_type, count(*)
    FROM inventory_movements
    WHERE order_id IS NOT NULL
    GROUP BY 1, 2, 3
    HAVING count(*) > 1;

and resolve any rows returned before running this migration.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f4a2e6b813"
down_revision: str | None = "b7d2e5a91c3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_inventory_movements_variant_order_type",
        "inventory_movements",
        ["product_variant_id", "order_id", "movement_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_inventory_movements_variant_order_type",
        "inventory_movements",
        type_="unique",
    )
