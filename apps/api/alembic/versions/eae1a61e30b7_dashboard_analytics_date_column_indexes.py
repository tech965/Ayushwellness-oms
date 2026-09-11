"""dashboard analytics date column indexes

Revision ID: eae1a61e30b7
Revises: b1e6d4f9a2c8
Create Date: 2026-09-11 11:16:43.676213

Follow-up to `f3a91c7d5e02` (`orders.order_datetime`), which fixed exactly
one of the several date/timestamp columns the Dashboard's analytics
endpoints (`app/services/analytics_service.py`) filter or sort by.
Confirmed live (EXPLAIN ANALYZE against the dev database, 96k customers):
`customers.created_at` alone -- queried unindexed, twice per `/analytics/
summary` request (current period + previous period) -- cost a measured
~100ms per scan. Every column below is read the same way: a plain
`WHERE <col> BETWEEN date_from AND date_to` or `ORDER BY <col> DESC LIMIT
N`, confirmed against the actual call sites in `analytics_service.py`
(`_summary_counts`, `get_breakdowns`, `get_returns_refunds_summary`,
`get_recent_activity`, `get_courier_performance`) -- not a blanket
"index every timestamp" pass. None of these columns had an index before
this migration (confirmed via `pg_indexes`).

Additive only -- no data change, no column type change, no other index
touched. Each index is created and dropped independently so a partial
failure/rollback never leaves an inconsistent set.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eae1a61e30b7"
down_revision: str | None = "b1e6d4f9a2c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) -- matches the exact columns `analytics_service.py`
# filters/sorts by; see this migration's own docstring for why each one
# is here (never a blanket "every timestamp column" pass).
_INDEXES: list[tuple[str, str]] = [
    ("customers", "created_at"),
    ("products", "created_at"),
    ("shipments", "updated_at"),
    ("shipments", "actual_delivery_date"),
    ("shipments", "created_at"),
    ("ndrs", "created_at"),
    ("rtos", "created_at"),
    ("returns", "created_at"),
    ("refunds", "created_at"),
    ("payments", "created_at"),
    ("orders", "created_at"),
]


def upgrade() -> None:
    for table, column in _INDEXES:
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)


def downgrade() -> None:
    for table, column in reversed(_INDEXES):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
