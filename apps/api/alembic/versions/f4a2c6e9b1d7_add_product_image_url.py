"""add product image_url

Revision ID: f4a2c6e9b1d7
Revises: e2f5b8a1c3d6
Create Date: 2026-09-10 09:00:00.000000

Adds `products.image_url` -- Shopify's `featuredImage.url`, pulled by the
existing product pull-sync (`ShopifyProductNormalizer`) so order line
items can show a real product thumbnail (e.g. the Telecaller order detail
page) instead of just a SKU/name.

Additive and nullable: every existing row reads NULL until the next
product sync backfills it naturally (no separate backfill step needed --
`upsert_synced_product` already overwrites this column on every sync, the
same way it already does `title`/`vendor`/`description`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a2c6e9b1d7"
down_revision: str | None = "e2f5b8a1c3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("image_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "image_url")
