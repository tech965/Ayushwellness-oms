"""manual bank verification evidence (Modules 6 & 7)

Revision ID: e7a1c3b5d9f4
Revises: d3b5f7a9c1e2
Create Date: 2026-11-26 12:00:00.000000

Additive and reversible. Two nullable columns: the bank reference an ADMIN
checked on a manual bank link, and the mandatory reason on a Shiprocket
remittance bank verification. Existing rows are left unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c3b5d9f4"
down_revision: str | None = "d3b5f7a9c1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bank_credit_links", sa.Column("bank_reference", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "cod_remittances",
        sa.Column("bank_verification_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cod_remittances", "bank_verification_reason")
    op.drop_column("bank_credit_links", "bank_reference")
