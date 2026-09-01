"""sync_jobs one active per entity partial unique index

Revision ID: d8f2c5a91b4e
Revises: c4e1a7f8b2d3
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8f2c5a91b4e"
down_revision: str | None = "c4e1a7f8b2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_sync_jobs_one_active_per_entity",
        "sync_jobs",
        ["integration_id", "entity_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_sync_jobs_one_active_per_entity", table_name="sync_jobs")
