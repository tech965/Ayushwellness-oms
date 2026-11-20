"""shiprocket wallet

Revision ID: b4d8e2a6c1f7
Revises: a3f9c1d7b2e4
Create Date: 2026-11-19 00:00:00.000000

Additive only: three new tables (balance snapshots, wallet transaction
ledger, pre-assignment wallet checks). No existing table is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4d8e2a6c1f7"
down_revision: str | None = "a3f9c1d7b2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUM = sa.Numeric(precision=14, scale=2)
DT = app.db.base.AwareDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("created_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    op.create_table(
        "shiprocket_wallet_snapshots",
        sa.Column("balance", NUM, nullable=False),
        sa.Column("fetched_at", DT, nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("raw_external_payload", JSON, nullable=True),
        *_base(),
    )
    op.create_index(
        "ix_shiprocket_wallet_snapshots_fetched_at", "shiprocket_wallet_snapshots", ["fetched_at"]
    )

    op.create_table(
        "shiprocket_wallet_transactions",
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("transaction_ref", sa.String(length=128), nullable=True),
        sa.Column("txn_date", DT, nullable=False),
        sa.Column("txn_type", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("balance_after", NUM, nullable=True),
        sa.Column("order_number", sa.String(length=64), nullable=True),
        sa.Column("awb", sa.String(length=64), nullable=True),
        sa.Column("courier_name", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payment_reference", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("synced_at", DT, nullable=True),
        sa.Column("raw_external_payload", JSON, nullable=True),
        sa.Column("manually_verified", sa.Boolean(), nullable=False),
        sa.Column("verified_by", app.db.base.GUID(), nullable=True),
        sa.Column("verified_at", DT, nullable=True),
        sa.Column("verification_note", sa.Text(), nullable=True),
        sa.Column("created_by", app.db.base.GUID(), nullable=True),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("dedup_key", name="uq_shiprocket_wallet_transactions_dedup_key"),
        *_base(),
    )
    for col in (
        "transaction_ref",
        "txn_date",
        "txn_type",
        "direction",
        "order_number",
        "awb",
        "courier_name",
        "status",
    ):
        op.create_index(
            f"ix_shiprocket_wallet_transactions_{col}", "shiprocket_wallet_transactions", [col]
        )

    op.create_table(
        "shiprocket_wallet_checks",
        sa.Column("shipment_ref", sa.String(length=64), nullable=True),
        sa.Column("order_number", sa.String(length=64), nullable=True),
        sa.Column("balance", NUM, nullable=True),
        sa.Column("estimated_charge", NUM, nullable=True),
        sa.Column("safety_buffer", NUM, nullable=False),
        sa.Column("required_minimum", NUM, nullable=True),
        sa.Column("shortfall", NUM, nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("checked_at", DT, nullable=False),
        *_base(),
    )
    op.create_index(
        "ix_shiprocket_wallet_checks_shipment_ref", "shiprocket_wallet_checks", ["shipment_ref"]
    )
    op.create_index("ix_shiprocket_wallet_checks_outcome", "shiprocket_wallet_checks", ["outcome"])
    op.create_index(
        "ix_shiprocket_wallet_checks_checked_at", "shiprocket_wallet_checks", ["checked_at"]
    )


def downgrade() -> None:
    op.drop_table("shiprocket_wallet_checks")
    op.drop_table("shiprocket_wallet_transactions")
    op.drop_table("shiprocket_wallet_snapshots")
