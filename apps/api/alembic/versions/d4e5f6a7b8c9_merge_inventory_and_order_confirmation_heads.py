"""merge inventory and order confirmation heads

Revision ID: d4e5f6a7b8c9
Revises: c1d4f8a63b57, c9f4a2e6b813
Create Date: 2026-09-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | tuple[str, str] | None = ("c1d4f8a63b57", "c9f4a2e6b813")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass