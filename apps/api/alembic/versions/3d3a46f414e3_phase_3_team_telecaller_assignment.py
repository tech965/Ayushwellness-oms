"""phase 3 team telecaller assignment

Revision ID: 3d3a46f414e3
Revises: 50337406e09a
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3d3a46f414e3"
down_revision: str | None = "50337406e09a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("team_leader_id", app.db.base.GUID(), nullable=True))
    op.create_index(op.f("ix_users_team_leader_id"), "users", ["team_leader_id"], unique=False)
    op.create_foreign_key(
        "fk_users_team_leader_id_users",
        "users",
        "users",
        ["team_leader_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "order_assignments",
        sa.Column("order_id", app.db.base.GUID(), nullable=False),
        sa.Column("assigned_to", app.db.base.GUID(), nullable=False),
        sa.Column("assigned_by", app.db.base.GUID(), nullable=True),
        sa.Column("assigned_at", app.db.base.AwareDateTime(timezone=True), nullable=False),
        sa.Column("team_leader_id", app.db.base.GUID(), nullable=True),
        sa.Column(
            "assignment_status",
            sa.Enum("active", "inactive", name="assignment_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("reassigned_from", app.db.base.GUID(), nullable=True),
        sa.Column("reassigned_to", app.db.base.GUID(), nullable=True),
        sa.Column("reassigned_at", app.db.base.AwareDateTime(timezone=True), nullable=True),
        sa.Column("reassignment_reason", sa.Text(), nullable=True),
        sa.Column(
            "current_status",
            sa.Enum(
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
                name="telecalling_status",
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
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reassigned_from"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reassigned_to"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_leader_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_order_assignments_assigned_by"), "order_assignments", ["assigned_by"], unique=False
    )
    op.create_index(
        op.f("ix_order_assignments_assigned_to"), "order_assignments", ["assigned_to"], unique=False
    )
    op.create_index(
        op.f("ix_order_assignments_assignment_status"),
        "order_assignments",
        ["assignment_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_order_assignments_current_status"),
        "order_assignments",
        ["current_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_order_assignments_next_follow_up_at"),
        "order_assignments",
        ["next_follow_up_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_order_assignments_order_id"), "order_assignments", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_order_assignments_team_leader_id"),
        "order_assignments",
        ["team_leader_id"],
        unique=False,
    )
    # One ACTIVE assignment per order at a time — defense-in-depth
    # alongside TelecallingService's single-transaction assign/reassign
    # logic (see app/models/telecalling.py).
    op.create_index(
        "uq_order_assignments_one_active_per_order",
        "order_assignments",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("assignment_status = 'active'"),
        sqlite_where=sa.text("assignment_status = 'active'"),
    )

    op.create_table(
        "call_attempts",
        sa.Column("order_id", app.db.base.GUID(), nullable=False),
        sa.Column("telecaller_id", app.db.base.GUID(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempted_at", app.db.base.AwareDateTime(timezone=True), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
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
                name="telecalling_status",
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
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["telecaller_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_call_attempts_order_id"), "call_attempts", ["order_id"], unique=False)
    op.create_index(
        op.f("ix_call_attempts_telecaller_id"), "call_attempts", ["telecaller_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_call_attempts_telecaller_id"), table_name="call_attempts")
    op.drop_index(op.f("ix_call_attempts_order_id"), table_name="call_attempts")
    op.drop_table("call_attempts")

    op.drop_index("uq_order_assignments_one_active_per_order", table_name="order_assignments")
    op.drop_index(op.f("ix_order_assignments_team_leader_id"), table_name="order_assignments")
    op.drop_index(op.f("ix_order_assignments_order_id"), table_name="order_assignments")
    op.drop_index(op.f("ix_order_assignments_next_follow_up_at"), table_name="order_assignments")
    op.drop_index(op.f("ix_order_assignments_current_status"), table_name="order_assignments")
    op.drop_index(op.f("ix_order_assignments_assignment_status"), table_name="order_assignments")
    op.drop_index(op.f("ix_order_assignments_assigned_to"), table_name="order_assignments")
    op.drop_index(op.f("ix_order_assignments_assigned_by"), table_name="order_assignments")
    op.drop_table("order_assignments")

    op.drop_constraint("fk_users_team_leader_id_users", "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_team_leader_id"), table_name="users")
    op.drop_column("users", "team_leader_id")

    # Enum types are dropped automatically with their owning tables on
    # SQLite; on Postgres they must be dropped explicitly once nothing
    # references them.
    sa.Enum(name="telecalling_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="assignment_status").drop(op.get_bind(), checkfirst=True)
