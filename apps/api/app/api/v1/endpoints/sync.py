"""sync endpoints.

Manual sync trigger only. This creates a `SyncJob` and hands it to
Celery; it never calls an external API from the request thread (spec
§9). Every `entity_type` runs through the generic provider-paginated
loop (`app.tasks.sync_tasks.execute_sync_task`) *except* `"tracking"`,
which is OMS-shipment-driven and runs through its own dedicated task —
see `app.integrations.shiprocket.sync` for why.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.integration import SyncJobResponse, TriggerSyncRequest
from app.schemas.response import ApiResponse
from app.services.sync_service import SyncService
from app.tasks.shiprocket_sync import refresh_tracking_task
from app.tasks.sync_tasks import execute_sync_task
from app.workers.celery_app import SHIPROCKET_QUEUE, queue_for_entity

router = APIRouter()
logger = get_logger(__name__)

_TASK_BY_ENTITY_TYPE: dict[str, Callable[[str], None]] = {
    "tracking": lambda job_id: refresh_tracking_task.apply_async(
        args=[job_id], queue=SHIPROCKET_QUEUE
    ),
}


def _enqueue(entity_type: str, job_id: str) -> None:
    dispatch = _TASK_BY_ENTITY_TYPE.get(entity_type)
    if dispatch is not None:
        dispatch(job_id)
    else:
        # `sync.execute` carries only the job id, so route it here by
        # entity_type (Shiprocket `shipments`/`ndr` -> the shiprocket
        # queue, everything else -> the default queue).
        execute_sync_task.apply_async(args=[job_id], queue=queue_for_entity(entity_type))


@router.post(
    "/{integration_id}/trigger", response_model=ApiResponse[SyncJobResponse], status_code=202
)
async def trigger_sync(
    integration_id: uuid.UUID,
    payload: TriggerSyncRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("sync_jobs.manage")),
) -> ApiResponse[SyncJobResponse]:
    job = await SyncService(session).start_sync(
        integration_id=integration_id,
        sync_type=payload.sync_type,
        entity_type=payload.entity_type,
        actor=current_user,
    )

    try:
        _enqueue(payload.entity_type, str(job.id))
    except Exception as exc:  # noqa: BLE001 - broker outage must not 500 an already-persisted job
        logger.warning("sync_task_enqueue_failed", sync_job_id=str(job.id), error=str(exc))
        return ApiResponse(
            data=SyncJobResponse.model_validate(job),
            message="Sync job created but could not be queued — background worker unreachable.",
        )

    return ApiResponse(data=SyncJobResponse.model_validate(job), message="Sync queued.")
