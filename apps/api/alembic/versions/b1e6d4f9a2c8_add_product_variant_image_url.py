"""add product_variants.image_url

Revision ID: b1e6d4f9a2c8
Revises: a8c3f1d5e7b2
Create Date: 2026-09-10 10:00:00.000000

Adds `product_variants.image_url` -- Shopify's per-variant image
(`ProductVariant.image.url` on the GraphQL Admin API), so the Inventory
UI can show a flavour/pack-specific photo (e.g. Aayush Wellness Herbal
Masala's Royal Tobacco / Ghutka / Paan Masala variants) when Shopify has
actually assigned one, instead of always falling back to the product's
single featured image (`products.image_url`, added by `f4a2c6e9b1d7`).

Additive and nullable: every existing row reads NULL until the next
product sync populates it naturally -- `ShopifyProductNormalizer` only
emits this key for a variant Shopify has actually given a distinct image,
so a variant with none simply stays NULL and the read side falls back to
the product image. No backfill, no data touched, no other column
affected.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1e6d4f9a2c8"
down_revision: str | None = "a8c3f1d5e7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_variants",
        sa.Column("image_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_variants", "image_url")
