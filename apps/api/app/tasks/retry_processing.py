"""Celery task: requeue retryable sync failures.

Scans unresolved `SyncError` rows across every integration and re-runs
the owning sync job for any error whose `error_type` is retryable and
hasn't exhausted `RetryPolicy.max_retries` (`app.integrations.retry`).
Provider-agnostic — works the same whether Phase 2 registers Shopify,
Shiprocket, or nothing at all.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.integrations.retry import should_retry
from app.models.integration import SyncError
from app.repositories.sync_error import SyncErrorRepository
from app.repositories.sync_job import SyncJobRepository
from app.services.sync_service import SyncService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _retry_failed_syncs() -> int:
    retried = 0
    async with AsyncSessionLocal() as session:
        errors_repo = SyncErrorRepository(session)
        sync_jobs = SyncJobRepository(session)
        sync_service = SyncService(session)

        result = await session.execute(select(SyncError).where(SyncError.resolved.is_(False)))
        for error in result.scalars().all():
            if not should_retry(error_type=error.error_type, retry_count=error.retry_count):
                continue

            job = await sync_jobs.get_by_id(error.sync_job_id)
            if job is None:
                continue

            await errors_repo.update(error, retry_count=error.retry_count + 1)
            await session.commit()

            await sync_service.run_sync(
                integration_id=job.integration_id,
                sync_type=job.sync_type,
                entity_type=job.entity_type,
            )
            retried += 1

    return retried


@celery_app.task(name="sync.retry_failed")
def retry_failed_syncs_task() -> int:
    count = asyncio.run(_retry_failed_syncs())
    logger.info("retry_failed_syncs_completed", retried=count)
    return count
