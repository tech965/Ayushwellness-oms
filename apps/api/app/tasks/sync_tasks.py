"""Celery tasks driving `SyncService` off the request thread (spec §9's
FastAPI -> SyncJob -> Celery -> Adapter pipeline).
"""

from __future__ import annotations

import asyncio
import uuid

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.services.sync_service import SyncService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _execute_sync(sync_job_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await SyncService(session).execute_sync(uuid.UUID(sync_job_id))


@celery_app.task(name="sync.execute")
def execute_sync_task(sync_job_id: str) -> None:
    """Runs an already-created (QUEUED) `SyncJob`. This is what the manual
    trigger endpoint (`POST /sync/{integration_id}/trigger`) enqueues,
    since it already created the job synchronously to return its id.
    """
    logger.info("sync_task_started", sync_job_id=sync_job_id)
    asyncio.run(_execute_sync(sync_job_id))


async def _run_sync(integration_id: str, sync_type: str, entity_type: str) -> None:
    async with AsyncSessionLocal() as session:
        await SyncService(session).run_sync(
            integration_id=uuid.UUID(integration_id),
            sync_type=sync_type,
            entity_type=entity_type,
        )


@celery_app.task(name="sync.run")
def run_sync_task(integration_id: str, sync_type: str, entity_type: str) -> None:
    """Creates and runs a new `SyncJob` in one step — for callers (Celery
    beat, `app.tasks.retry_processing`) that don't already have a job id.
    """
    logger.info("sync_task_started", integration_id=integration_id, entity_type=entity_type)
    asyncio.run(_run_sync(integration_id, sync_type, entity_type))


@celery_app.task(name="sync.run_scheduled")
def run_scheduled_sync_task() -> None:
    """Periodic entrypoint reserved for Celery beat. Iterating `enabled`
    integrations and calling `run_sync_task` per one is Phase 2 work —
    doing so now against an empty adapter registry would just churn
    QUEUED->FAILED `SyncJob` rows for no reason, so this is a documented
    no-op until a real adapter exists.
    """
    logger.info("scheduled_sync_task_noop")
