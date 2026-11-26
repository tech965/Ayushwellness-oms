"""gateway API synchronisation (Module 7, Cashfree)

Revision ID: d3b5f7a9c1e2
Revises: c9e1a5d3f7b2
Create Date: 2026-11-24 12:00:00.000000

Additive and reversible. Adds four nullable/defaulted columns to
`gateway_settlement_lines` (source, cf_settlement_id, event_type,
synced_at) and one new table, `gateway_sync_states` (last sync / last
success / lock per gateway). Existing uploaded rows keep source='upload'.
No financial row is written.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3b5f7a9c1e2"
down_revision: str | None = "c9e1a5d3f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = app.db.base.AwareDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
GUID = app.db.base.GUID()


def upgrade() -> None:
    with op.batch_alter_table("gateway_settlement_lines") as batch:
        batch.add_column(
            sa.Column("source", sa.String(length=16), nullable=False, server_default="upload")
        )
        batch.add_column(sa.Column("cf_settlement_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("event_type", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("synced_at", DT, nullable=True))
    op.create_index(
        "ix_gateway_settlement_lines_cf_settlement_id",
        "gateway_settlement_lines",
        ["cf_settlement_id"],
    )

    op.create_table(
        "gateway_sync_states",
        sa.Column("gateway", sa.String(length=32), nullable=False),
        sa.Column("last_attempt_at", DT, nullable=True),
        sa.Column("last_success_at", DT, nullable=True),
        sa.Column("last_status", sa.String(length=24), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("last_trigger", sa.String(length=16), nullable=True),
        sa.Column("synced_through", DT, nullable=True),
        sa.Column("last_result", JSON, nullable=True),
        sa.Column("last_webhook_at", DT, nullable=True),
        sa.Column("lock_until", DT, nullable=True),
        sa.Column("id", GUID, nullable=False),
        sa.Column("created_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gateway", name="uq_gateway_sync_states_gateway"),
    )


def downgrade() -> None:
    op.drop_table("gateway_sync_states")
    op.drop_index(
        "ix_gateway_settlement_lines_cf_settlement_id", table_name="gateway_settlement_lines"
    )
    with op.batch_alter_table("gateway_settlement_lines") as batch:
        batch.drop_column("synced_at")
        batch.drop_column("event_type")
        batch.drop_column("cf_settlement_id")
        batch.drop_column("source")
