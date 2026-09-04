"""abandoned checkouts and checkout telecalling assignment

Revision ID: 7a1c9f2d5e4b
Revises: 00deb0255b64
Create Date: 2026-09-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7a1c9f2d5e4b"
down_revision: str | None = "00deb0255b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Reused verbatim from the "phase 3 team telecaller assignment" migration
# (3d3a46f414e3) — `checkout_assignments`/`checkout_call_attempts` share
# the exact same calling-status/assignment-status vocabulary as
# `order_assignments`/`call_attempts`, so this must be the *same* Postgres
# enum type, not a second `CREATE TYPE` under a different name.
_TELECALLING_STATUS_VALUES = (
    "not_called",
    "call_attempted",
    "connected",
    "not_received",
    "busy",
    "switched_off",
    "invalid_number",
    "call_back_requested",
    "interested",
    "not_interested",
    "follow_up_required",
    "confirmed",
    "cancelled",
)


def upgrade() -> None:
    op.create_table(
        "abandoned_checkouts",
        sa.Column("source_system", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("external_created_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("external_updated_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("sync_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "raw_external_payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("shopify_checkout_id", sa.String(length=64), nullable=True),
        sa.Column("customer_id", app.db.base.GUID(), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("customer_phone", sa.String(length=32), nullable=True),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=8), server_default="INR", nullable=False),
        sa.Column("subtotal_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column(
            "line_items",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("is_recovered", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("checkout_created_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("checkout_updated_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system", "external_id", name="uq_abandoned_checkouts_source_external_id"
        ),
        sa.UniqueConstraint("shopify_checkout_id"),
    )
    op.create_index(
        op.f("ix_abandoned_checkouts_source_system"),
        "abandoned_checkouts",
        ["source_system"],
        unique=False,
    )
    op.create_index(
        op.f("ix_abandoned_checkouts_shopify_checkout_id"),
        "abandoned_checkouts",
        ["shopify_checkout_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_abandoned_checkouts_customer_id"),
        "abandoned_checkouts",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_abandoned_checkouts_customer_phone"),
        "abandoned_checkouts",
        ["customer_phone"],
        unique=False,
    )

    op.create_table(
        "checkout_assignments",
        sa.Column("checkout_id", app.db.base.GUID(), nullable=False),
        sa.Column("assigned_to", app.db.base.GUID(), nullable=False),
        sa.Column("assigned_by", app.db.base.GUID(), nullable=True),
        sa.Column("assigned_at", app.db.base.AwareDateTime(timezone=True), nullable=False),
        sa.Column("team_leader_id", app.db.base.GUID(), nullable=True),
        sa.Column(
            "assignment_status",
            postgresql.ENUM("active", "inactive", name="assignment_status", create_type=False),
            server_default="active",
            nullable=False,
        ),
        sa.Column("reassigned_from", app.db.base.GUID(), nullable=True),
        sa.Column("reassigned_to", app.db.base.GUID(), nullable=True),
        sa.Column("reassigned_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("reassignment_reason", sa.Text(), nullable=True),
        sa.Column(
            "current_status",
            postgresql.ENUM(
                *_TELECALLING_STATUS_VALUES, name="telecalling_status", create_type=False
            ),
            server_default="not_called",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("next_follow_up_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["checkout_id"], ["abandoned_checkouts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reassigned_from"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reassigned_to"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_leader_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_checkout_assignments_assigned_by"),
        "checkout_assignments",
        ["assigned_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_checkout_assignments_assigned_to"),
        "checkout_assignments",
        ["assigned_to"],
        unique=False,
    )
    op.create_index(
        op.f("ix_checkout_assignments_assignment_status"),
        "checkout_assignments",
        ["assignment_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_checkout_assignments_current_status"),
        "checkout_assignments",
        ["current_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_checkout_assignments_next_follow_up_at"),
        "checkout_assignments",
        ["next_follow_up_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_checkout_assignments_checkout_id"),
        "checkout_assignments",
        ["checkout_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_checkout_assignments_team_leader_id"),
        "checkout_assignments",
        ["team_leader_id"],
        unique=False,
    )
    op.create_index(
        "uq_checkout_assignments_one_active_per_checkout",
        "checkout_assignments",
        ["checkout_id"],
        unique=True,
        postgresql_where=sa.text("assignment_status = 'active'"),
        sqlite_where=sa.text("assignment_status = 'active'"),
    )

    op.create_table(
        "checkout_call_attempts",
        sa.Column("checkout_id", app.db.base.GUID(), nullable=False),
        sa.Column("telecaller_id", app.db.base.GUID(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempted_at", app.db.base.AwareDateTime(timezone=True), nullable=False),
        sa.Column(
            "outcome",
            postgresql.ENUM(
                *_TELECALLING_STATUS_VALUES, name="telecalling_status", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("next_follow_up_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["checkout_id"], ["abandoned_checkouts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["telecaller_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_checkout_call_attempts_checkout_id"),
        "checkout_call_attempts",
        ["checkout_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_checkout_call_attempts_telecaller_id"),
        "checkout_call_attempts",
        ["telecaller_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_checkout_call_attempts_telecaller_id"), table_name="checkout_call_attempts"
    )
    op.drop_index(
        op.f("ix_checkout_call_attempts_checkout_id"), table_name="checkout_call_attempts"
    )
    op.drop_table("checkout_call_attempts")

    op.drop_index(
        "uq_checkout_assignments_one_active_per_checkout", table_name="checkout_assignments"
    )
    op.drop_index(op.f("ix_checkout_assignments_team_leader_id"), table_name="checkout_assignments")
    op.drop_index(op.f("ix_checkout_assignments_checkout_id"), table_name="checkout_assignments")
    op.drop_index(
        op.f("ix_checkout_assignments_next_follow_up_at"), table_name="checkout_assignments"
    )
    op.drop_index(op.f("ix_checkout_assignments_current_status"), table_name="checkout_assignments")
    op.drop_index(
        op.f("ix_checkout_assignments_assignment_status"), table_name="checkout_assignments"
    )
    op.drop_index(op.f("ix_checkout_assignments_assigned_to"), table_name="checkout_assignments")
    op.drop_index(op.f("ix_checkout_assignments_assigned_by"), table_name="checkout_assignments")
    op.drop_table("checkout_assignments")

    op.drop_index(op.f("ix_abandoned_checkouts_customer_phone"), table_name="abandoned_checkouts")
    op.drop_index(op.f("ix_abandoned_checkouts_customer_id"), table_name="abandoned_checkouts")
    op.drop_index(
        op.f("ix_abandoned_checkouts_shopify_checkout_id"), table_name="abandoned_checkouts"
    )
    op.drop_index(op.f("ix_abandoned_checkouts_source_system"), table_name="abandoned_checkouts")
    op.drop_table("abandoned_checkouts")

    # `assignment_status`/`telecalling_status` are NOT dropped here — both
    # enum types are still owned by (and required by) `order_assignments`/
    # `call_attempts` from migration 3d3a46f414e3, which this migration
    # never touches.
