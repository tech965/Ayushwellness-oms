"""Storage audit (API-first / minimal-storage initiative).

Read-only -- never writes, never deletes, safe against production or a
production replica. Two kinds of measurement:

1. **Real Postgres storage** (`_audit_database_size`/`_audit_table_sizes`/
   `_audit_largest_indexes`, added for Phase 2 production verification):
   total database size, per-table total/heap/index/TOAST breakdown, live
   vs dead tuple counts (bloat/vacuum signal) from `pg_stat_user_tables`,
   and the largest individual indexes -- straight from Postgres's own
   catalogs (`pg_class`/`pg_stat_user_tables`/`pg_stat_user_indexes`), so
   this section only produces real numbers against actual Postgres; it
   is a no-op shape against SQLite (not used in this codebase outside
   tests, which don't run this script).
2. **`raw_external_payload` duplication** (Phase 1, `_audit_raw_payload_
   table`): for every `SyncMetadataMixin` table, how many rows carry a
   payload and how many bytes that one column occupies
   (`pg_column_size`) -- the specific duplication the retention endpoint
   (`app.services.retention_service`) targets. For the append-only
   history tables (`order_events`, `shipment_events`, `webhook_events`,
   `audit_logs`, `payment_transactions`, `sync_jobs`, `sync_errors`)
   prints row counts and age buckets -- measured only, never acted on by
   this script (that needs its own, separately-approved cleanup
   migration once these numbers justify it).

No historical snapshots exist yet -- there is no "growth since last
time" to report. Save this script's own output (e.g. redirect to a
timestamped file) each time it runs to start building that trend.

Run with: python scripts/audit_storage.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.integration import SyncError, SyncJob, WebhookEvent
from app.models.order import OrderEvent
from app.models.payment import PaymentTransaction
from app.models.shipment import ShipmentEvent
from app.services.retention_service import RETAINABLE_TABLES
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

AGE_BUCKET_DAYS = (90, 180, 365)

_DATABASE_SIZE_QUERY = text("SELECT pg_database_size(current_database())")

# `pg_total_relation_size` = heap + indexes + TOAST (+ free space map/
# visibility map, negligible). Reported separately below so a table
# dominated by TOAST (large JSONB payloads, e.g. `customers`) is
# visibly distinguishable from one dominated by its own indexes.
_TABLE_SIZE_QUERY = text(
    """
    SELECT
        c.relname AS table_name,
        pg_total_relation_size(c.oid) AS total_bytes,
        pg_relation_size(c.oid) AS heap_bytes,
        pg_indexes_size(c.oid) AS index_bytes,
        COALESCE(pg_total_relation_size(c.reltoastrelid), 0) AS toast_bytes,
        COALESCE(s.n_live_tup, 0) AS live_tuples,
        COALESCE(s.n_dead_tup, 0) AS dead_tuples,
        s.last_autovacuum,
        s.last_vacuum
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE c.relkind = 'r' AND n.nspname = 'public'
    ORDER BY total_bytes DESC
    """
)

_LARGEST_INDEXES_QUERY = text(
    """
    SELECT
        indexrelname AS index_name,
        relname AS table_name,
        pg_relation_size(indexrelid) AS index_bytes
    FROM pg_stat_user_indexes
    ORDER BY index_bytes DESC
    LIMIT 10
    """
)


async def _audit_database_size(session: AsyncSession) -> None:
    size_bytes = (await session.execute(_DATABASE_SIZE_QUERY)).scalar_one()
    print(f"Total database size: {size_bytes / (1024**2):,.1f} MiB ({size_bytes:,} bytes)")


async def _audit_table_sizes(session: AsyncSession) -> None:
    rows = (await session.execute(_TABLE_SIZE_QUERY)).mappings().all()
    print(
        f"  {'table':<28} {'total MiB':>10} {'heap MiB':>10} {'idx MiB':>9} "
        f"{'toast MiB':>10} {'live':>9} {'dead':>8}  last_autovacuum"
    )
    for row in rows:
        mib = 1024**2
        print(
            f"  {row['table_name']:<28} "
            f"{row['total_bytes'] / mib:>10.1f} "
            f"{row['heap_bytes'] / mib:>10.1f} "
            f"{row['index_bytes'] / mib:>9.1f} "
            f"{row['toast_bytes'] / mib:>10.1f} "
            f"{row['live_tuples']:>9} "
            f"{row['dead_tuples']:>8}  "
            f"{row['last_autovacuum'] or 'never'}"
        )


async def _audit_largest_indexes(session: AsyncSession) -> None:
    rows = (await session.execute(_LARGEST_INDEXES_QUERY)).mappings().all()
    for row in rows:
        print(
            f"  {row['index_name']:<40} on {row['table_name']:<22} "
            f"{row['index_bytes'] / (1024**2):>8.1f} MiB"
        )

APPEND_ONLY_TABLES: dict[str, type[Any]] = {
    "order_events": OrderEvent,
    "shipment_events": ShipmentEvent,
    "webhook_events": WebhookEvent,
    "audit_logs": AuditLog,
    "payment_transactions": PaymentTransaction,
    "sync_jobs": SyncJob,
    "sync_errors": SyncError,
}


async def _audit_raw_payload_table(session: AsyncSession, table: str, model: type[Any]) -> None:
    row_count = (
        await session.execute(select(func.count()).select_from(model))
    ).scalar_one()
    if row_count == 0:
        print(f"  {table:<22} 0 rows")
        return

    non_null_count, total_bytes, avg_bytes = (
        await session.execute(
            select(
                func.count(model.raw_external_payload),
                func.coalesce(func.sum(func.pg_column_size(model.raw_external_payload)), 0),
                func.coalesce(func.avg(func.pg_column_size(model.raw_external_payload)), 0),
            ).where(model.raw_external_payload.is_not(None))
        )
    ).one()

    print(
        f"  {table:<22} {row_count:>8} rows | "
        f"{non_null_count:>8} with raw_external_payload | "
        f"{total_bytes / 1024:>10.1f} KiB total | "
        f"{avg_bytes:>6.0f} B avg"
    )


async def _audit_append_only_table(session: AsyncSession, table: str, model: type[Any]) -> None:
    row_count = (
        await session.execute(select(func.count()).select_from(model))
    ).scalar_one()
    if row_count == 0:
        print(f"  {table:<22} 0 rows")
        return

    oldest, newest = (
        await session.execute(select(func.min(model.created_at), func.max(model.created_at)))
    ).one()

    bucket_counts = []
    now = datetime.now(UTC)
    for days in AGE_BUCKET_DAYS:
        cutoff = now - timedelta(days=days)
        count = (
            await session.execute(
                select(func.count()).select_from(model).where(model.created_at < cutoff)
            )
        ).scalar_one()
        bucket_counts.append((days, count))

    buckets_str = " | ".join(f">{days}d: {count}" for days, count in bucket_counts)
    print(
        f"  {table:<22} {row_count:>8} rows | oldest {oldest} | newest {newest} | {buckets_str}"
    )


async def main() -> None:
    async with AsyncSessionLocal() as session:
        print("=== Database size ===")
        await _audit_database_size(session)

        print()
        print("=== Per-table storage (total / heap / indexes / TOAST, dead tuples) ===")
        await _audit_table_sizes(session)

        print()
        print("=== 10 largest indexes ===")
        await _audit_largest_indexes(session)

        print()
        print("=== SyncMetadataMixin tables (raw_external_payload) ===")
        for table, model in sorted(RETAINABLE_TABLES.items()):
            await _audit_raw_payload_table(session, table, model)

        print()
        print("=== Append-only history tables (row-count / age only) ===")
        for table, model in sorted(APPEND_ONLY_TABLES.items()):
            await _audit_append_only_table(session, table, model)


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
