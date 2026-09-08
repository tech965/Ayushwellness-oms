"""call attempt edit/delete via correction rows

Revision ID: b7c3e9f21a04
Revises: a4e9d3c7f158
Create Date: 2026-09-08 00:00:00.000000

Adds `call_attempts.corrects_attempt_id` (nullable self-FK) and
`call_attempts.is_void` (nullable-safe boolean, default false) so the
Telecaller order page's Edit/Delete actions can be implemented as new
correction rows rather than mutating or removing history — `CallAttempt`
stays append-only (see its model docstring). Both columns are nullable/
defaulted, so every existing row is unaffected and reads back exactly as
before.
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c3e9f21a04"
down_revision: str | None = "a4e9d3c7f158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "call_attempts",
        sa.Column("corrects_attempt_id", app.db.base.GUID(), nullable=True),
    )
    op.add_column(
        "call_attempts",
        sa.Column("is_void", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index(
        "ix_call_attempts_corrects_attempt_id",
        "call_attempts",
        ["corrects_attempt_id"],
    )
    op.create_foreign_key(
        "fk_call_attempts_corrects_attempt_id_call_attempts",
        "call_attempts",
        "call_attempts",
        ["corrects_attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_call_attempts_corrects_attempt_id_call_attempts",
        "call_attempts",
        type_="foreignkey",
    )
    op.drop_index("ix_call_attempts_corrects_attempt_id", table_name="call_attempts")
    op.drop_column("call_attempts", "is_void")
    op.drop_column("call_attempts", "corrects_attempt_id")
