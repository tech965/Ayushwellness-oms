"""add catalog_variants (OMS-visible grouping layer)

Revision ID: d3b8f1e2a5c7
Revises: c2a9f4e1b8d7
Create Date: 2026-09-09 14:00:00.000000

Adds the OMS-visible catalog-variant grouping layer:

  * new table `catalog_variants` (one row per OMS-visible variant,
    belongs to exactly one product), and
  * `product_variants.catalog_variant_id` -- a nullable FK mapping each
    underlying Shopify `ProductVariant` to at most one `CatalogVariant`.

STRICTLY SCHEMA ONLY. This migration:
  - creates NO catalog_variants rows,
  - assigns NO `catalog_variant_id` values,
  - does not touch `available_quantity`, `inventory_quantity`, orders,
    order_items, inventory_movements, SKUs, or Shopify ids.

Every existing `product_variant` row keeps `catalog_variant_id = NULL`,
which the read layer surfaces as an implicit single-member OMS variant --
i.e. behaviour is unchanged until an explicit, reviewed data-assignment
step populates the groupings.

`ProductVariant.catalog_variant_id` is `ON DELETE SET NULL`: removing a
CatalogVariant only un-groups its members, it never deletes a
ProductVariant or cascades to `inventory_movements`.

Shopify sync never writes `catalog_variant_id` (the normalizer doesn't
emit it), so a resync cannot clobber a grouping assignment.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3b8f1e2a5c7"
down_revision: str | None = "c2a9f4e1b8d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_variants",
        sa.Column("product_id", app.db.base.GUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "name", name="uq_catalog_variants_product_name"),
    )
    op.create_index(
        op.f("ix_catalog_variants_product_id"), "catalog_variants", ["product_id"], unique=False
    )

    op.add_column(
        "product_variants",
        sa.Column("catalog_variant_id", app.db.base.GUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_product_variants_catalog_variant_id"),
        "product_variants",
        ["catalog_variant_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_product_variants_catalog_variant_id_catalog_variants",
        "product_variants",
        "catalog_variants",
        ["catalog_variant_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_product_variants_catalog_variant_id_catalog_variants",
        "product_variants",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_product_variants_catalog_variant_id"), table_name="product_variants")
    op.drop_column("product_variants", "catalog_variant_id")

    op.drop_index(op.f("ix_catalog_variants_product_id"), table_name="catalog_variants")
    op.drop_table("catalog_variants")
