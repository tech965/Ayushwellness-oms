"""merge confirmation fulfillment and catalog variants heads

Revision ID: e2f5b8a1c3d6
Revises: b3e7a1c9f4d2, d3b8f1e2a5c7
Create Date: 2026-09-09 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e2f5b8a1c3d6"
down_revision: str | tuple[str, str] | None = ("b3e7a1c9f4d2", "d3b8f1e2a5c7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
