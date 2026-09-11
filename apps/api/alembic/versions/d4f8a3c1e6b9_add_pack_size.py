"""add product_variants.pack_size

Revision ID: d4f8a3c1e6b9
Revises: 9c12c76f3342
Create Date: 2026-09-11 00:00:00.000000

Additive only. `pack_size` defaults to 1 for every existing row --
existing `product_variants.available_quantity` values, and every past
`InventoryMovement`, are left completely untouched (no reinterpretation,
no backfill by any assumed pack size). Combined with the existing
`packets_per_box`, `InventoryService` converts an order line to boxes as
`ceil(quantity * pack_size / packets_per_box)` going forward only -- see
`app.models.product.ProductVariant.pack_size`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f8a3c1e6b9"
down_revision: str | None = "9c12c76f3342"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_variants",
        sa.Column("pack_size", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_product_variants_pack_size_positive",
        "product_variants",
        "pack_size > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_product_variants_pack_size_positive", "product_variants", type_="check"
    )
    op.drop_column("product_variants", "pack_size")
