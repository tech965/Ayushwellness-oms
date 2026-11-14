"""Lightweight, on-demand storage health snapshot (Phase 8, cost-
optimization initiative). Exposes a small, cheap subset of what
`scripts/audit_storage.py` measures as a real API response instead of a
script a human has to run manually — total database size, the largest
tables by total size, and append-only history table row counts/age
buckets.

Read-only, no writes, no locks. Every query here is one of the same
cheap catalog/aggregate queries `scripts/audit_storage.py` already proved
safe against production-sized tables (catalog lookups and indexed
`func.count()`/`func.min`/`func.max` aggregates) — nothing here does a
full-table scan of a payload column itself, unlike that script's
`raw_external_payload` byte-size audit (deliberately left script-only; a
`pg_column_size` aggregate over every row is too expensive to run
on-demand from a request).

Dialect-aware: the size queries are Postgres catalog functions
(`pg_database_size`, `pg_total_relation_size`, ...), so against the
SQLite test database `database_size_mb`/`tables` come back empty/`None`
rather than erroring — the append-only row-count/age-bucket section works
identically on both dialects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.integration import SyncError, SyncJob, WebhookEvent
from app.models.order import OrderEvent
from app.models.payment import PaymentTransaction
from app.models.shipment import ShipmentEvent

AGE_BUCKET_DAYS = (90, 180, 365)

APPEND_ONLY_TABLES: dict[str, type[Any]] = {
    "order_events": OrderEvent,
    "shipment_events": ShipmentEvent,
    "webhook_events": WebhookEvent,
    "audit_logs": AuditLog,
    "payment_transactions": PaymentTransaction,
    "sync_jobs": SyncJob,
    "sync_errors": SyncError,
}

_DATABASE_SIZE_QUERY = text("SELECT pg_database_size(current_database())")

_TOP_TABLES_QUERY = text(
    """
    SELECT
        c.relname AS table_name,
        pg_total_relation_size(c.oid) AS total_bytes,
        pg_relation_size(c.oid) AS heap_bytes,
        pg_indexes_size(c.oid) AS index_bytes,
        COALESCE(pg_total_relation_size(c.reltoastrelid), 0) AS toast_bytes,
        COALESCE(s.n_live_tup, 0) AS live_tuples,
        COALESCE(s.n_dead_tup, 0) AS dead_tuples
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE c.relkind = 'r' AND n.nspname = 'public'
    ORDER BY total_bytes DESC
    LIMIT :limit
    """
)


def _is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


async def get_database_size_mb(session: AsyncSession) -> float | None:
    if not _is_postgres(session):
        return None
    size_bytes = (await session.execute(_DATABASE_SIZE_QUERY)).scalar_one()
    return round(size_bytes / (1024**2), 2)


async def get_top_tables(session: AsyncSession, *, limit: int = 10) -> list[dict[str, Any]]:
    if not _is_postgres(session):
        return []
    rows = (await session.execute(_TOP_TABLES_QUERY, {"limit": limit})).mappings().all()
    mib = 1024**2
    return [
        {
            "table": row["table_name"],
            "total_mb": round(row["total_bytes"] / mib, 2),
            "heap_mb": round(row["heap_bytes"] / mib, 2),
            "index_mb": round(row["index_bytes"] / mib, 2),
            "toast_mb": round(row["toast_bytes"] / mib, 2),
            "live_tuples": row["live_tuples"],
            "dead_tuples": row["dead_tuples"],
        }
        for row in rows
    ]


async def get_append_only_summary(session: AsyncSession) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    summary: list[dict[str, Any]] = []
    for table, model in sorted(APPEND_ONLY_TABLES.items()):
        row_count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
        buckets = {}
        for days in AGE_BUCKET_DAYS:
            cutoff = now - timedelta(days=days)
            buckets[f"older_than_{days}d"] = (
                await session.execute(
                    select(func.count()).select_from(model).where(model.created_at < cutoff)
                )
            ).scalar_one()
        summary.append({"table": table, "row_count": row_count, **buckets})
    return summary
