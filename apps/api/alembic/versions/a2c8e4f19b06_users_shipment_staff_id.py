"""users shipment_staff_id

Revision ID: a2c8e4f19b06
Revises: f6b2c9a3d7e1
Create Date: 2026-09-11 00:00:00.000000

Additive only -- one new nullable self-referential FK column on the
existing `users` table (`shipment_staff_id`), mirroring `team_leader_id`
exactly (same nullable/index/ondelete shape). Set on a Telecaller row:
which Shipment Staff user handles that Telecaller's confirmed orders.
Every existing user simply reads NULL ("not scoped to any Shipment
Staff yet") -- accurate for all current data, no backfill needed.

Nothing in application code reads this column before this migration is
applied (see `app/services/shipment_staff_service.py`).
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2c8e4f19b06"
down_revision: str | None = "f6b2c9a3d7e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("shipment_staff_id", app.db.base.GUID(), nullable=True),
    )
    op.create_index(
        "ix_users_shipment_staff_id",
        "users",
        ["shipment_staff_id"],
    )
    op.create_foreign_key(
        "fk_users_shipment_staff_id_users",
        "users",
        "users",
        ["shipment_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_shipment_staff_id_users", "users", type_="foreignkey")
    op.drop_index("ix_users_shipment_staff_id", table_name="users")
    op.drop_column("users", "shipment_staff_id")
