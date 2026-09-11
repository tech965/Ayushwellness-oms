"""order shipping address validation

Revision ID: e8eacf148c4f
Revises: eae1a61e30b7
Create Date: 2026-09-11 12:00:00.000000

Adds a Shiprocket-style address-validation cluster to `orders` --
`app.services.address_validation_service.AddressValidationService`.
A DIFFERENT concept from the existing `shipping_address_sync_*` columns
(that cluster is the outbound OMS -> Shopify push state for the same
address; this one is "is the address itself any good," never pushed
anywhere).

Additive and fully nullable: every existing order reads every new
column as `NULL` (shown as "Validation pending" in the UI) until it's
next created/updated/explicitly validated -- no backfill, no other
column touched, no data change to any existing row.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8eacf148c4f"
down_revision: str | None = "eae1a61e30b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_ENUM_NAME = "address_validation_status"


def upgrade() -> None:
    status_enum = sa.Enum("valid", "ambiguous", "junk", "unknown", name=_STATUS_ENUM_NAME)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "orders", sa.Column("shipping_address_validation_status", status_enum, nullable=True)
    )
    op.add_column(
        "orders", sa.Column("shipping_address_validation_score", sa.Integer(), nullable=True)
    )
    op.add_column(
        "orders", sa.Column("shipping_address_validation_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column(
            "shipping_address_validated_at", app.db.base.AwareDateTime(), nullable=True
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "shipping_address_validation_provider_ref", sa.String(length=255), nullable=True
        ),
    )
    op.add_column(
        "orders",
        sa.Column("shipping_address_validation_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "shipping_address_validation_hash")
    op.drop_column("orders", "shipping_address_validation_provider_ref")
    op.drop_column("orders", "shipping_address_validated_at")
    op.drop_column("orders", "shipping_address_validation_reason")
    op.drop_column("orders", "shipping_address_validation_score")
    op.drop_column("orders", "shipping_address_validation_status")
    sa.Enum(name=_STATUS_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
