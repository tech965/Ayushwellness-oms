"""marketplace movements: per-CatalogVariant scope + reversal/replacement links

Revision ID: d9a4b7c2e5f1
Revises: c7d2e8f4a1b6
Create Date: 2026-09-21 12:00:00.000000

Two additive changes to `product_marketplace_movements`:

1. VARIANT SCOPE. The ledger was keyed `(product_id, platform)`, which
   cannot tell Herbal Masala's Gold/Red/Blue apart. New nullable
   `catalog_variant_id` (FK -> catalog_variants, ON DELETE CASCADE):
   NULL = product-scoped balance (every product with fewer than two
   CatalogVariants -- all existing rows), set = that OMS-visible
   variant's own balance. The balance key becomes
   `(product_id, catalog_variant_id, platform)`. It is NEVER an
   underlying 60/120/180 SKU.

2. EDIT / UNDO WITHOUT MUTATION. Rows are never updated or deleted.
   New nullable self-referencing FKs (ON DELETE RESTRICT, so an original
   can never be deleted out from under its correction):
     * `reverses_movement_id` (UNIQUE): set on a REVERSAL row that
       cancels an earlier Sale/RTO -- an original is reversed at most once.
     * `replaces_movement_id` (UNIQUE): set on the replacement row an Edit
       writes after its reversal -- an original is replaced at most once.
   New enum value `reversal` for `product_marketplace_movement_type`.

EXISTING DATA: every existing row keeps `catalog_variant_id`,
`reverses_movement_id` and `replaces_movement_id` NULL (columns are added
nullable, no default, no backfill), i.e. remains a product-scoped,
never-corrected original. No row is inserted, updated or deleted;
`product_variants`, `inventory_movements`, orders, shipments and the OMS
stock ledger are untouched.

DOWNGRADE drops the three columns and their constraints/indexes. The
`reversal` enum value is left in place (Postgres cannot drop an enum
value), which is harmless; a downgrade after Edit/Undo has been used
discards the reversal/replacement LINKS, so only downgrade before use.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a4b7c2e5f1"
down_revision: str | None = "c7d2e8f4a1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "product_marketplace_movements"


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must be committed before the new value is
    # usable, so it runs in its own autocommit block, first.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE product_marketplace_movement_type ADD VALUE IF NOT EXISTS 'reversal'"
        )

    op.add_column(_TABLE, sa.Column("catalog_variant_id", app.db.base.GUID(), nullable=True))
    op.add_column(_TABLE, sa.Column("reverses_movement_id", app.db.base.GUID(), nullable=True))
    op.add_column(_TABLE, sa.Column("replaces_movement_id", app.db.base.GUID(), nullable=True))

    op.create_foreign_key(
        "fk_product_marketplace_movements_catalog_variant_id",
        _TABLE,
        "catalog_variants",
        ["catalog_variant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_product_marketplace_movements_reverses",
        _TABLE,
        _TABLE,
        ["reverses_movement_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_product_marketplace_movements_replaces",
        _TABLE,
        _TABLE,
        ["replaces_movement_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_product_marketplace_movements_reverses", _TABLE, ["reverses_movement_id"]
    )
    op.create_unique_constraint(
        "uq_product_marketplace_movements_replaces", _TABLE, ["replaces_movement_id"]
    )
    op.create_index(
        op.f("ix_product_marketplace_movements_catalog_variant_id"),
        _TABLE,
        ["catalog_variant_id"],
    )
    op.create_index(
        "ix_product_marketplace_movements_scope_platform_date",
        _TABLE,
        ["product_id", "catalog_variant_id", "platform", "stock_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_marketplace_movements_scope_platform_date", table_name=_TABLE)
    op.drop_index(op.f("ix_product_marketplace_movements_catalog_variant_id"), table_name=_TABLE)
    op.drop_constraint("uq_product_marketplace_movements_replaces", _TABLE, type_="unique")
    op.drop_constraint("uq_product_marketplace_movements_reverses", _TABLE, type_="unique")
    op.drop_constraint("fk_product_marketplace_movements_replaces", _TABLE, type_="foreignkey")
    op.drop_constraint("fk_product_marketplace_movements_reverses", _TABLE, type_="foreignkey")
    op.drop_constraint(
        "fk_product_marketplace_movements_catalog_variant_id", _TABLE, type_="foreignkey"
    )
    op.drop_column(_TABLE, "replaces_movement_id")
    op.drop_column(_TABLE, "reverses_movement_id")
    op.drop_column(_TABLE, "catalog_variant_id")
