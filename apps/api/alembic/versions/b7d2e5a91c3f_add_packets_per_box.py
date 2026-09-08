"""add product_variants.packets_per_box

Revision ID: b7d2e5a91c3f
Revises: a4e9d3c7f158
Create Date: 2026-09-08 00:00:00.000000

Additive only. `packets_per_box` defaults to 1 for every existing row --
existing `product_variants.available_quantity` values are left completely
untouched (no reinterpretation, no backfill by any assumed pack size).
`available_quantity` continues to represent boxes going forward (see
`app.models.product.ProductVariant`), exposed as `available_boxes` at the
API layer; the underlying column is deliberately not renamed to avoid an
unnecessary migration risk.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e5a91c3f"
down_revision: str | None = "a4e9d3c7f158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_variants",
        sa.Column("packets_per_box", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_product_variants_packets_per_box_positive",
        "product_variants",
        "packets_per_box > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_product_variants_packets_per_box_positive",
        "product_variants",
        type_="check",
    )
    op.drop_column("product_variants", "packets_per_box")
