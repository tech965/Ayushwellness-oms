"""reconciliation endpoints — Phase 2.4.

Triggering a run only creates the `ReconciliationRun` row and hands it to
Celery (spec §11/§27: never a long-running provider-calling operation on
the request thread) — mirrors `app.api.v1.endpoints.sync.trigger_sync`,
including the broker-unavailable fallback so a Redis outage still leaves
a queryable, honestly-reported row instead of a fake success.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.reconciliation import ReconciliationResultResponse, ReconciliationRunResponse
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.reconciliation_service import ReconciliationService
from app.tasks.reconciliation_tasks import run_reconciliation_task

router = APIRouter()
logger = get_logger(__name__)


@router.post("/runs", response_model=ApiResponse[ReconciliationRunResponse], status_code=202)
async def trigger_reconciliation_run(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[ReconciliationRunResponse]:
    run = await ReconciliationService(session).start_run(actor=current_user)

    try:
        run_reconciliation_task.delay(str(run.id))
    except Exception as exc:  # noqa: BLE001 - broker outage must not 500 an already-persisted run
        logger.warning("reconciliation_task_enqueue_failed", run_id=str(run.id), error=str(exc))
        return ApiResponse(
            data=ReconciliationRunResponse.model_validate(run),
            message=(
                "Reconciliation run created but could not be queued — background worker "
                "unreachable."
            ),
        )

    return ApiResponse(
        data=ReconciliationRunResponse.model_validate(run), message="Reconciliation queued."
    )


@router.get("/runs", response_model=PaginatedResponse[ReconciliationRunResponse])
async def list_reconciliation_runs(
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> PaginatedResponse[ReconciliationRunResponse]:
    items, total = await ReconciliationService(session).list_runs(
        page_params=page_params, sort_params=sort_params
    )
    return PaginatedResponse(
        data=[ReconciliationRunResponse.model_validate(r) for r in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/runs/{run_id}", response_model=ApiResponse[ReconciliationRunResponse])
async def get_reconciliation_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[ReconciliationRunResponse]:
    run = await ReconciliationService(session).get_run(run_id)
    return ApiResponse(data=ReconciliationRunResponse.model_validate(run))


@router.get("/results", response_model=PaginatedResponse[ReconciliationResultResponse])
async def list_reconciliation_results(
    run_id: uuid.UUID | None = None,
    status: str | None = None,
    check_type: str | None = None,
    provider: str | None = None,
    resolved: bool | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> PaginatedResponse[ReconciliationResultResponse]:
    items, total = await ReconciliationService(session).list_results(
        page_params=page_params,
        sort_params=sort_params,
        run_id=run_id,
        status=status,
        check_type=check_type,
        provider=provider,
        resolved=resolved,
    )
    return PaginatedResponse(
        data=[ReconciliationResultResponse.model_validate(r) for r in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.post(
    "/results/{result_id}/resolve", response_model=ApiResponse[ReconciliationResultResponse]
)
async def resolve_reconciliation_result(
    result_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[ReconciliationResultResponse]:
    result = await ReconciliationService(session).resolve_result(result_id, actor=current_user)
    return ApiResponse(data=ReconciliationResultResponse.model_validate(result))
