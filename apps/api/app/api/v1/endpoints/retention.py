"""Operator-triggered, non-destructive retention action for the
`SyncMetadataMixin.raw_external_payload` JSONB column (API-first /
minimal-storage initiative, Phase 1). Dry-run by default; never drops a
column, a table, or a row -- see `app.services.retention_service` for
exactly what this does and does not touch, and `scripts/audit_storage.py`
for measuring the actual impact before running this for real.

Gated on `integrations.manage` -- this is an integration-data-storage
operation, not a per-domain one, so it reuses that existing permission
rather than introducing a new one for a single Phase-1 action.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.response import ApiResponse
from app.services.audit_service import AuditService
from app.services.retention_service import (
    RETAINABLE_TABLES,
    RetentionError,
    null_out_stale_raw_payloads,
)
from app.services.storage_health_service import (
    get_append_only_summary,
    get_database_size_mb,
    get_top_tables,
)

router = APIRouter()


class TableSizeInfo(BaseModel):
    table: str
    total_mb: float
    heap_mb: float
    index_mb: float
    toast_mb: float
    live_tuples: int
    dead_tuples: int


class AppendOnlyTableInfo(BaseModel):
    table: str
    row_count: int
    older_than_90d: int
    older_than_180d: int
    older_than_365d: int


class StorageHealthResponse(BaseModel):
    dialect: str
    database_size_mb: float | None
    largest_tables: list[TableSizeInfo]
    append_only_tables: list[AppendOnlyTableInfo]


class RawPayloadRetentionRequest(BaseModel):
    table: str
    older_than_days: int = Field(gt=0)
    dry_run: bool = True


class RawPayloadRetentionResponse(BaseModel):
    table: str
    older_than_days: int
    dry_run: bool
    rows_affected: int


@router.get("/raw-payloads/tables", response_model=ApiResponse[list[str]])
async def list_retainable_tables(
    _: User = Depends(require_permission("integrations.manage")),
) -> ApiResponse[list[str]]:
    return ApiResponse(data=sorted(RETAINABLE_TABLES))


@router.get("/storage-health", response_model=ApiResponse[StorageHealthResponse])
async def get_storage_health(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("integrations.manage")),
) -> ApiResponse[StorageHealthResponse]:
    """On-demand storage snapshot (Phase 8) -- total database size and the
    10 largest tables (Postgres catalog lookups only against production;
    empty/`None` against the SQLite test database, never an error), plus
    every append-only history table's row count and age buckets. No
    automatic schedule -- call this whenever a human wants a fresh look.
    """
    dialect = session.get_bind().dialect.name
    database_size_mb = await get_database_size_mb(session)
    largest_tables = await get_top_tables(session, limit=10)
    append_only = await get_append_only_summary(session)
    return ApiResponse(
        data=StorageHealthResponse(
            dialect=dialect,
            database_size_mb=database_size_mb,
            largest_tables=[TableSizeInfo(**row) for row in largest_tables],
            append_only_tables=[AppendOnlyTableInfo(**row) for row in append_only],
        )
    )


@router.post("/raw-payloads/run", response_model=ApiResponse[RawPayloadRetentionResponse])
async def run_raw_payload_retention(
    payload: RawPayloadRetentionRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("integrations.manage")),
) -> ApiResponse[RawPayloadRetentionResponse]:
    try:
        rows_affected = await null_out_stale_raw_payloads(
            session,
            table=payload.table,
            older_than_days=payload.older_than_days,
            dry_run=payload.dry_run,
        )
    except RetentionError as exc:
        raise ValidationError(str(exc)) from exc

    if payload.dry_run:
        # No write happened -- nothing to commit or audit.
        await session.rollback()
    else:
        await AuditService(session).record(
            user=current_user,
            action="retention.raw_payloads_cleared",
            entity_type=payload.table,
            entity_id=payload.table,
            new_value={"older_than_days": payload.older_than_days, "rows_affected": rows_affected},
        )
        await session.commit()

    return ApiResponse(
        data=RawPayloadRetentionResponse(
            table=payload.table,
            older_than_days=payload.older_than_days,
            dry_run=payload.dry_run,
            rows_affected=rows_affected,
        ),
        message=(
            f"{rows_affected} row(s) would be affected (dry run)."
            if payload.dry_run
            else f"Cleared raw_external_payload on {rows_affected} row(s)."
        ),
    )
