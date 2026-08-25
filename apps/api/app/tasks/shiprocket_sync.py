"""Celery task: Shiprocket tracking refresh.

OMS-shipment-driven (see `app.integrations.shiprocket.sync`) — the only
task in this codebase whose `SyncJob` lifecycle isn't driven by
`SyncService.execute_sync`'s generic provider-paginated loop, since
Shiprocket has no such feed for tracking. Uses the same `SyncJob`
lifecycle primitives everything else does, so it's indistinguishable
from any other sync in `/integrations/{id}`'s sync history.
"""

from __future__ import annotations

import asyncio
import uuid

from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.sync import refresh_tracking
from app.models.integration import IntegrationCode
from app.repositories.sync_job import SyncJobRepository
from app.services.sync_service import SyncService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _execute_tracking_refresh(sync_job_id: str) -> None:
    async with AsyncSessionLocal() as session:
        job_uuid = uuid.UUID(sync_job_id)
        job = await SyncJobRepository(session).get_by_id(job_uuid)
        if job is None:
            logger.warning("shiprocket_tracking_refresh_job_not_found", sync_job_id=sync_job_id)
            return

        sync_service = SyncService(session)
        await sync_service.mark_running(job_uuid)

        adapter = get_adapter(IntegrationCode.SHIPROCKET)
        if not isinstance(adapter, ShiprocketAdapter):
            await sync_service.record_error(
                job_uuid,
                entity_type="tracking",
                error_type="integration_error",
                error_message="No adapter registered for integration 'shiprocket'.",
            )
            await sync_service.complete_sync(job_uuid, success=False)
            return

        try:
            await refresh_tracking(session, job_uuid, adapter)
        except IntegrationError as exc:
            await sync_service.record_error(
                job_uuid,
                entity_type="tracking",
                error_type=exc.details.get("error_type", "integration_error"),
                error_message=exc.message,
            )
            await sync_service.complete_sync(job_uuid, success=False)
            return

        await sync_service.complete_sync(job_uuid, success=True)


@celery_app.task(name="shiprocket.refresh_tracking")
def refresh_tracking_task(sync_job_id: str) -> None:
    logger.info("shiprocket_tracking_refresh_started", sync_job_id=sync_job_id)
    asyncio.run(_execute_tracking_refresh(sync_job_id))
