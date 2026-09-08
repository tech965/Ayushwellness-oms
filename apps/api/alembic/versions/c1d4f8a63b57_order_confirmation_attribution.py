"""order confirmation telecaller attribution

Revision ID: c1d4f8a63b57
Revises: b7c3e9f21a04
Create Date: 2026-09-09 00:00:00.000000

Adds `orders.confirmed_by_telecaller_id` (nullable FK -> users.id) and
`orders.confirmed_at` (nullable timestamp) so the telecaller who moves an
order from PENDING to CONFIRMED (`OrderService.confirm_order`) stays
permanently attributed to it, independent of `OrderAssignment.assigned_to`
(which changes on reassignment). Both columns are nullable with no
backfill — every existing order simply reads NULL ("not confirmed via
this workflow"), which is accurate for historical data.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d4f8a63b57"
down_revision: str | None = "b7c3e9f21a04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("confirmed_by_telecaller_id", app.db.base.GUID(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_orders_confirmed_by_telecaller_id",
        "orders",
        ["confirmed_by_telecaller_id"],
    )
    op.create_foreign_key(
        "fk_orders_confirmed_by_telecaller_id_users",
        "orders",
        "users",
        ["confirmed_by_telecaller_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_orders_confirmed_by_telecaller_id_users", "orders", type_="foreignkey"
    )
    op.drop_index("ix_orders_confirmed_by_telecaller_id", table_name="orders")
    op.drop_column("orders", "confirmed_at")
    op.drop_column("orders", "confirmed_by_telecaller_id")
