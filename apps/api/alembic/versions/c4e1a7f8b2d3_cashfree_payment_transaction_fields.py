"""cashfree payment transaction fields

Revision ID: c4e1a7f8b2d3
Revises: 3d3a46f414e3
Create Date: 2026-08-30 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4e1a7f8b2d3"
down_revision: str | None = "3d3a46f414e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    # Defense-in-depth idempotency backstop for gateway callbacks (e.g.
    # Cashfree webhooks/reconciliation), on top of the existing
    # WebhookEvent-level dedup — see app.models.payment.PaymentTransaction.
    op.create_unique_constraint(
        "uq_payment_transactions_gateway_txn",
        "payment_transactions",
        ["gateway", "gateway_transaction_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_payment_transactions_gateway_txn", "payment_transactions", type_="unique"
    )
    op.drop_column("payment_transactions", "raw_payload")
