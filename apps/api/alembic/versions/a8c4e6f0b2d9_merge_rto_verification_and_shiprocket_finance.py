"""merge: RTO warehouse verification + payment reconciliation chain

Revision ID: a8c4e6f0b2d9
Revises: 7f3a9c2e5d81, f2b8d4a6c0e1
Create Date: 2026-11-26 19:00:00.000000

Joins the two branches that both start from `b4d8e2a6c1f7`:

* `7f3a9c2e5d81` -- RTO warehouse verification / legacy order reservation
* `c9e1a5d3f7b2 -> d3b5f7a9c1e2 -> e7a1c3b5d9f4 -> f2b8d4a6c0e1` -- payment
  reconciliation, Cashfree sync, manual bank verification, Shiprocket finance

The two touch different tables, so this revision changes no schema. No
existing migration is edited.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a8c4e6f0b2d9"
down_revision: str | Sequence[str] | None = ("7f3a9c2e5d81", "f2b8d4a6c0e1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
