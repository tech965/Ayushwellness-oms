"""webhook_deliveries: minimal idempotency ledger (no payloads)

Revision ID: c5a1d7e3f9b2
Revises: b4f8a2c6d1e9
Create Date: 2026-11-08 00:00:00.000000

Additive. Stores only (provider, delivery_id, topic, received_at) so
duplicate webhook deliveries are detected across restarts and workers
without persisting any payload. Old `webhook_events` rows are untouched.
"""

from __future__ import annotations

import app.db.base
import sqlalchemy as sa
from alembic import op

revision = "c5a1d7e3f9b2"
down_revision = "b4f8a2c6d1e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("delivery_id", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column(
            "received_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "delivery_id", name="uq_webhook_deliveries_provider_delivery"
        ),
    )
    op.create_index("ix_webhook_deliveries_received_at", "webhook_deliveries", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_received_at", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
