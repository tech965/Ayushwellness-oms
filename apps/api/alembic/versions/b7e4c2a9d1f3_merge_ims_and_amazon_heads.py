"""merge IMS and Amazon heads

Revision ID: b7e4c2a9d1f3
Revises: a1b2c3d4e5f6, d8f3a1c6e9b4
Create Date: 2026-11-15 00:00:00.000000

No-op merge: the IMS migration (`a1b2c3d4e5f6`) and the Amazon integration
migration (`d8f3a1c6e9b4`) both descend from `f1b7d3a9c5e2` and diverged into
two heads once both branches reached `main`. This reconciles them into one
head so `alembic upgrade head` is unambiguous.
"""

from __future__ import annotations

revision = "b7e4c2a9d1f3"
down_revision = ("a1b2c3d4e5f6", "d8f3a1c6e9b4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
