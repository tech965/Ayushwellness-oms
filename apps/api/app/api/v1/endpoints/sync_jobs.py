"""sync-jobs endpoints — read-only monitoring across every integration.

Mutating a sync job happens only through `SyncService`
(`app.tasks.sync_tasks`, `app.tasks.retry_processing`), never via the API.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.dependencies.pagination import sort_params as sort_params_dep
from app.models.auth import User
from app.repositories.sync_job import SyncJobRepository
from app.schemas.common import PageParams, SortParams, build_pagination_meta
from app.schemas.integration import SyncJobResponse
from app.schemas.response import ApiResponse, PaginatedResponse

router = APIRouter()


@router.get("", response_model=PaginatedResponse[SyncJobResponse])
async def list_sync_jobs(
    page_params: PageParams = Depends(pagination_params),
    sort_params: SortParams = Depends(sort_params_dep),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("sync_jobs.read")),
) -> PaginatedResponse[SyncJobResponse]:
    items, total = await SyncJobRepository(session).list(
        page_params=page_params, sort_params=sort_params
    )
    return PaginatedResponse(
        data=[SyncJobResponse.model_validate(j) for j in items],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


@router.get("/{sync_job_id}", response_model=ApiResponse[SyncJobResponse])
async def get_sync_job(
    sync_job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("sync_jobs.read")),
) -> ApiResponse[SyncJobResponse]:
    job = await SyncJobRepository(session).get_by_id(sync_job_id)
    if job is None:
        raise NotFoundError("Sync job not found.")
    return ApiResponse(data=SyncJobResponse.model_validate(job))
