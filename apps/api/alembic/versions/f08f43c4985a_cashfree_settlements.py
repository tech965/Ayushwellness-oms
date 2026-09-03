"""cashfree settlements

Revision ID: f08f43c4985a
Revises: f3a91c7d5e02
Create Date: 2026-09-03 14:50:35.822053

Additive only -- new table, no existing table/column touched. Backs
`CashfreeSettlement` (app.models.cashfree_settlement), populated only by
`CashfreeSyncService.sync_settlements` from `POST /pg/settlements` --
deliberately separate from `payments`/`payment_transactions` (spec: a
settlement is not a customer payment, never mix the two).
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f08f43c4985a"
down_revision: str | None = "f3a91c7d5e02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cashfree_settlements",
        sa.Column("cf_settlement_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("status_description", sa.String(length=255), nullable=True),
        sa.Column("settlement_utr", sa.String(length=255), nullable=True),
        sa.Column(
            "settlement_initiated_on", app.db.base.AwareDateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "settlement_processed_on", app.db.base.AwareDateTime(timezone=True), nullable=True
        ),
        sa.Column("payment_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("pg_service_charge", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("pg_service_tax", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("adjustment", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("settlement_charge", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("settlement_tax", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("amount_settled", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "raw_external_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            app.db.base.AwareDateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cf_settlement_id", name="uq_cashfree_settlements_cf_settlement_id"),
    )
    op.create_index(
        op.f("ix_cashfree_settlements_cf_settlement_id"),
        "cashfree_settlements",
        ["cf_settlement_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cashfree_settlements_settlement_processed_on"),
        "cashfree_settlements",
        ["settlement_processed_on"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cashfree_settlements_settlement_utr"),
        "cashfree_settlements",
        ["settlement_utr"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cashfree_settlements_status"), "cashfree_settlements", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_cashfree_settlements_status"), table_name="cashfree_settlements")
    op.drop_index(
        op.f("ix_cashfree_settlements_settlement_utr"), table_name="cashfree_settlements"
    )
    op.drop_index(
        op.f("ix_cashfree_settlements_settlement_processed_on"),
        table_name="cashfree_settlements",
    )
    op.drop_index(
        op.f("ix_cashfree_settlements_cf_settlement_id"), table_name="cashfree_settlements"
    )
    op.drop_table("cashfree_settlements")
