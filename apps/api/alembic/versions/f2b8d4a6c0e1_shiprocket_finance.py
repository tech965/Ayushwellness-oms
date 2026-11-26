"""shiprocket shipment charges and COD payout summaries

Revision ID: f2b8d4a6c0e1
Revises: e7a1c3b5d9f4
Create Date: 2026-11-26 18:00:00.000000

Additive and reversible: two new tables. No existing table is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2b8d4a6c0e1"
down_revision: str | None = "e7a1c3b5d9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = app.db.base.AwareDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
GUID = app.db.base.GUID()
MONEY = sa.Numeric(14, 2)
WEIGHT = sa.Numeric(10, 3)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", GUID, nullable=False),
        sa.Column("created_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ]


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "shiprocket_shipment_charges",
        sa.Column("awb", sa.String(length=64), nullable=False),
        sa.Column("shiprocket_order_id", sa.String(length=64), nullable=False),
        sa.Column("order_number", sa.String(length=64), nullable=True),
        sa.Column("courier_name", sa.String(length=128), nullable=True),
        sa.Column("zone", sa.String(length=32), nullable=True),
        sa.Column("freight_charges", MONEY, nullable=True),
        sa.Column("cod_charges", MONEY, nullable=True),
        sa.Column("applied_weight_amount", MONEY, nullable=True),
        sa.Column("charged_weight_amount", MONEY, nullable=True),
        sa.Column("charged_weight_amount_rto", MONEY, nullable=True),
        sa.Column("applied_weight_amount_rto", MONEY, nullable=True),
        sa.Column("applied_weight", WEIGHT, nullable=True),
        sa.Column("charged_weight", WEIGHT, nullable=True),
        sa.Column("has_charges", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", DT, nullable=False),
        sa.Column("raw_external_payload", JSON, nullable=True),
        sa.UniqueConstraint("awb", name="uq_shiprocket_shipment_charges_awb"),
        *_base(),
    )
    _index("shiprocket_shipment_charges", "order_number")
    _index("shiprocket_shipment_charges", "fetched_at")
    op.create_table(
        "shiprocket_payouts",
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("crf_id", sa.String(length=64), nullable=True),
        sa.Column("utr", sa.String(length=128), nullable=True),
        sa.Column("payout_date", DT, nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("cod_amount", MONEY, nullable=True),
        sa.Column("freight_deduction", MONEY, nullable=True),
        sa.Column("other_deductions", MONEY, nullable=True),
        sa.Column("net_amount", MONEY, nullable=False),
        sa.Column("file_id", GUID, nullable=True),
        sa.Column("raw_row", JSON, nullable=True),
        sa.ForeignKeyConstraint(["file_id"], ["settlement_files.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("dedupe_key", name="uq_shiprocket_payouts_dedupe_key"),
        *_base(),
    )
    for column in ("crf_id", "utr", "payout_date", "status", "file_id"):
        _index("shiprocket_payouts", column)


def downgrade() -> None:
    op.drop_table("shiprocket_payouts")
    op.drop_table("shiprocket_shipment_charges")
