"""merge address validation and catalog variant stock adjustments heads

Revision ID: 9c12c76f3342
Revises: a7c4e9d2f156, e8eacf148c4f
Create Date: 2026-09-11 17:31:47.281583
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c12c76f3342'
down_revision: Union[str, None] = ('a7c4e9d2f156', 'e8eacf148c4f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
