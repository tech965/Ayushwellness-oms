"""cod remittances

Revision ID: a3f9c1d7b2e4
Revises: c3d9f1a7e5b2
Create Date: 2026-11-18 00:00:00.000000

Additive only: one new table backing `CodRemittance`.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3f9c1d7b2e4"
down_revision: str | None = "c3d9f1a7e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    num = sa.Numeric(precision=14, scale=2)
    dt = app.db.base.AwareDateTime(timezone=True)
    op.create_table(
        "cod_remittances",
        sa.Column("awb", sa.String(length=64), nullable=False),
        sa.Column("shiprocket_order_id", sa.String(length=64), nullable=True),
        sa.Column("channel_order_id", sa.String(length=64), nullable=True),
        sa.Column("cod_amount", num, nullable=True),
        sa.Column("collected_amount", num, nullable=True),
        sa.Column("collected_at", dt, nullable=True),
        sa.Column("remitted_amount", num, nullable=True),
        sa.Column("remittance_status", sa.String(length=64), nullable=True),
        sa.Column("remittance_reference", sa.String(length=128), nullable=True),
        sa.Column("settlement_date", dt, nullable=True),
        sa.Column("synced_at", dt, nullable=True),
        sa.Column(
            "raw_external_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("bank_credit_amount", num, nullable=True),
        sa.Column("bank_credit_reference", sa.String(length=128), nullable=True),
        sa.Column("bank_credited_on", dt, nullable=True),
        sa.Column("bank_verified_at", dt, nullable=True),
        sa.Column("bank_verified_by", app.db.base.GUID(), nullable=True),
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("created_at", dt, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", dt, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bank_verified_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cod_remittances_awb", "cod_remittances", ["awb"], unique=True)
    op.create_index("ix_cod_remittances_channel_order_id", "cod_remittances", ["channel_order_id"])
    op.create_index(
        "ix_cod_remittances_remittance_status", "cod_remittances", ["remittance_status"]
    )
    op.create_index("ix_cod_remittances_settlement_date", "cod_remittances", ["settlement_date"])


def downgrade() -> None:
    op.drop_table("cod_remittances")
