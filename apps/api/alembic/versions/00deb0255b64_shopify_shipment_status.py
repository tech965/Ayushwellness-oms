"""shopify shipment status

Revision ID: 00deb0255b64
Revises: 46369de746b1
Create Date: 2026-09-03 16:46:51.115682

Additive only -- one new nullable column on the existing `orders` table.
Backs `Order.shopify_shipment_status` (app.models.order), populated by
`ShopifyOrderNormalizer` from `Fulfillment.displayStatus` (the actual
Shopify delivery/shipment-progress status) -- deliberately separate from
the pre-existing `orders.fulfillment_status` column, which stays mapped
to Shopify's much coarser `Order.displayFulfillmentStatus`
(unfulfilled/partially_fulfilled/fulfilled). The two must never be
conflated in the Orders UI.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00deb0255b64"
down_revision: str | None = "46369de746b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("shopify_shipment_status", sa.String(length=50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orders", "shopify_shipment_status")
