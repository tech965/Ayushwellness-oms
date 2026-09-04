"""merge chat_query_logs and abandoned_checkouts heads

Revision ID: 9c1f4b6a2d70
Revises: 7a1c9f2d5e4b, e7a1c9d4f2b6
Create Date: 2026-09-04 00:00:00.000000

No-op merge -- `7a1c9f2d5e4b` (abandoned checkouts/checkout telecalling,
from origin/main) and `e7a1c9d4f2b6` (chat_query_logs, from this branch)
both descend from `d8f2c5a91b4e` but diverged into two independent heads
once the branches were merged in git. Reconciles them into one head so
`alembic upgrade head` and any migration added after this one have a
single parent again.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "9c1f4b6a2d70"
down_revision: str | tuple[str, ...] | None = ("7a1c9f2d5e4b", "e7a1c9d4f2b6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
