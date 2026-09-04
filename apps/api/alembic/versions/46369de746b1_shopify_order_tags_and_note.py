"""shopify order tags and note

Revision ID: 46369de746b1
Revises: f08f43c4985a
Create Date: 2026-09-03 15:43:48.817625

Additive only -- two new nullable columns on the existing `orders` table,
no other table touched. Backs `Order.shopify_tags`/`Order.shopify_order_note`
(app.models.order), populated by `ShopifyOrderNormalizer` from the
GraphQL `Order.tags`/`Order.note` fields -- deliberately separate from
the pre-existing `orders.notes` column, which is OMS-internal staff
free text and never touched by Shopify sync.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "46369de746b1"
down_revision: str | None = "f08f43c4985a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "shopify_tags",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column("orders", sa.Column("shopify_order_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "shopify_order_note")
    op.drop_column("orders", "shopify_tags")
