"""let catalog_variant_stock_adjustments carry product-level rows

Revision ID: c7d2e8f4a1b6
Revises: b5e9c3a1d7f4
Create Date: 2026-09-21 00:00:00.000000

A marketplace Sale/RTO must change the product's OMS total stock without
choosing a SKU. The existing OMS-total ledger
(`catalog_variant_stock_adjustments`) is keyed by `catalog_variant_id`
(NOT NULL), which is only deterministic when a product has exactly ONE
OMS-visible variant. For a product with several CatalogVariants (Herbal
Masala: Gold/Red/Blue) or none (ungrouped products) any single
CatalogVariant would be an arbitrary allocation -- so this migration
extends the SAME ledger (one ledger, one sum, no second source of truth)
to allow a PRODUCT-scoped row:

  * `catalog_variant_id` becomes nullable.
  * new nullable `product_id` FK -> products.id (ON DELETE CASCADE).
  * new nullable `product_marketplace_movement_id` FK ->
    product_marketplace_movements.id (ON DELETE CASCADE), UNIQUE: links a
    marketplace-driven row to the marketplace event that caused it.
  * CHECK: exactly one of (catalog_variant_id, product_id) is set.

EXISTING DATA: every existing row has `catalog_variant_id` set and the two
new columns NULL (they are added nullable with no default), so every row
already satisfies the CHECK. No row is updated, inserted, or deleted.
`product_variants`, `inventory_movements`, orders, shipments, and
`product_marketplace_movements` rows are untouched (this only adds an FK
referencing the latter's primary key).
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d2e8f4a1b6"
down_revision: str | None = "b5e9c3a1d7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "catalog_variant_stock_adjustments"


def upgrade() -> None:
    op.alter_column(_TABLE, "catalog_variant_id", existing_type=app.db.base.GUID(), nullable=True)
    op.add_column(_TABLE, sa.Column("product_id", app.db.base.GUID(), nullable=True))
    op.add_column(
        _TABLE, sa.Column("product_marketplace_movement_id", app.db.base.GUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_cv_stock_adjustments_product_id_products",
        _TABLE,
        "products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_cv_stock_adjustments_marketplace_movement_id",
        _TABLE,
        "product_marketplace_movements",
        ["product_marketplace_movement_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_catalog_variant_stock_adjustments_product_id"), _TABLE, ["product_id"])
    op.create_unique_constraint(
        "uq_cv_stock_adjustments_product_marketplace_movement_id",
        _TABLE,
        ["product_marketplace_movement_id"],
    )
    op.create_check_constraint(
        "ck_cv_stock_adjustments_exactly_one_scope",
        _TABLE,
        "(catalog_variant_id IS NOT NULL AND product_id IS NULL) "
        "OR (catalog_variant_id IS NULL AND product_id IS NOT NULL)",
    )


def downgrade() -> None:
    # Product-scoped rows have no catalog_variant_id, so restoring NOT NULL
    # requires removing them first (they only exist once this feature was
    # used); rows with a catalog_variant_id are never touched.
    op.drop_constraint("ck_cv_stock_adjustments_exactly_one_scope", _TABLE, type_="check")
    op.execute(f"DELETE FROM {_TABLE} WHERE catalog_variant_id IS NULL")
    op.drop_constraint(
        "uq_cv_stock_adjustments_product_marketplace_movement_id", _TABLE, type_="unique"
    )
    op.drop_index(op.f("ix_catalog_variant_stock_adjustments_product_id"), table_name=_TABLE)
    op.drop_constraint(
        "fk_cv_stock_adjustments_marketplace_movement_id", _TABLE, type_="foreignkey"
    )
    op.drop_constraint("fk_cv_stock_adjustments_product_id_products", _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "product_marketplace_movement_id")
    op.drop_column(_TABLE, "product_id")
    op.alter_column(_TABLE, "catalog_variant_id", existing_type=app.db.base.GUID(), nullable=False)
