"""phase 2.4 reconciliation

Revision ID: 50337406e09a
Revises: 1b440c092593
Create Date: 2026-08-23 20:58:05.835632
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "50337406e09a"
down_revision: str | None = "1b440c092593"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("triggered_by_user_id", app.db.base.GUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("running", "completed", "failed", name="reconciliation_run_status"),
            server_default="running",
            nullable=False,
        ),
        sa.Column("started_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("completed_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("total_checked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reconciled_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("mismatch_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "run_metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            app.db.base.AwareDateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_reconciliation_runs_status"), "reconciliation_runs", ["status"], unique=False
    )
    op.create_table(
        "reconciliation_results",
        sa.Column("run_id", app.db.base.GUID(), nullable=False),
        sa.Column("check_type", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("internal_id", sa.String(length=255), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column(
            "expected_value",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "actual_value",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum("reconciled", "mismatch", "missing", "error", name="reconciliation_status"),
            nullable=False,
        ),
        sa.Column("message", sa.String(length=1000), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("resolved_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", app.db.base.GUID(), nullable=True),
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            app.db.base.AwareDateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["reconciliation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "check_type",
            "internal_id",
            "external_id",
            name="uq_reconciliation_results_run_check_entity",
        ),
    )
    op.create_index(
        op.f("ix_reconciliation_results_check_type"),
        "reconciliation_results",
        ["check_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_results_entity_type"),
        "reconciliation_results",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_results_external_id"),
        "reconciliation_results",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_results_internal_id"),
        "reconciliation_results",
        ["internal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_results_provider"),
        "reconciliation_results",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_results_resolved"),
        "reconciliation_results",
        ["resolved"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_results_run_id"), "reconciliation_results", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_reconciliation_results_status"),
        "reconciliation_results",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reconciliation_results_status"), table_name="reconciliation_results")
    op.drop_index(op.f("ix_reconciliation_results_run_id"), table_name="reconciliation_results")
    op.drop_index(op.f("ix_reconciliation_results_resolved"), table_name="reconciliation_results")
    op.drop_index(op.f("ix_reconciliation_results_provider"), table_name="reconciliation_results")
    op.drop_index(
        op.f("ix_reconciliation_results_internal_id"), table_name="reconciliation_results"
    )
    op.drop_index(
        op.f("ix_reconciliation_results_external_id"), table_name="reconciliation_results"
    )
    op.drop_index(
        op.f("ix_reconciliation_results_entity_type"), table_name="reconciliation_results"
    )
    op.drop_index(op.f("ix_reconciliation_results_check_type"), table_name="reconciliation_results")
    op.drop_table("reconciliation_results")
    op.drop_index(op.f("ix_reconciliation_runs_status"), table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
