"""add catalog title override (manual display name)

Revision ID: c2a9f4e1b8d7
Revises: a2c8e4f19b06
Create Date: 2026-09-09 12:00:00.000000

Adds `products.title_override` and `product_variants.title_override` --
an optional, OMS-authoritative *display name* that shadows the
Shopify-synced `title` in the Inventory UI when set.

`title` stays exactly as Shopify sends it (so reconciliation and every
existing search/report is unchanged); `title_override` is written only
by staff via the Inventory "Edit Name" action and is NEVER produced by
`ShopifyProductNormalizer`, so `ProductService.upsert_synced_product` /
`BaseRepository.upsert_by_external_id` never touch it on a resync -- the
same protection-by-omission pattern that already keeps
`available_quantity` / `packets_per_box` safe from Shopify.

Additive and nullable: every existing row reads NULL ("no custom name --
show the Shopify title"), so there is no backfill and the downgrade is a
plain column drop.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2a9f4e1b8d7"
down_revision: str | None = "a2c8e4f19b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # widths match each table's existing `title` column
    op.add_column(
        "products",
        sa.Column("title_override", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "product_variants",
        sa.Column("title_override", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_variants", "title_override")
    op.drop_column("products", "title_override")
