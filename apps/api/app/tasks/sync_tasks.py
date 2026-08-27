"""Celery tasks driving `SyncService` off the request thread (spec §9's
FastAPI -> SyncJob -> Celery -> Adapter pipeline).
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, dispose_engine_sync
from app.integrations.registry import get_adapter
from app.models.enums import SyncType
from app.models.integration import Integration, IntegrationCode
from app.services.sync_service import SyncService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Which entity types each provider's adapter actually supports syncing
# generically (matches what `ShopifyAdapter`/`ShiprocketAdapter.fetch()`
# accept — Shiprocket's generic `fetch()` only ever supported "ndr";
# tracking refresh runs through its own dedicated task, not this path).
# A provider with no adapter registered (Blue Dart/Delhivery/Ecom
# Express/WhatsApp/Meta/Instagram — no real adapter exists for any of
# them yet) is intentionally absent here, not just filtered out at
# runtime, so adding one is a one-line change instead of a silent
# behavior change to what already runs.
_SCHEDULED_SYNC_ENTITIES: dict[str, list[str]] = {
    IntegrationCode.SHOPIFY: ["orders", "customers", "products"],
    IntegrationCode.SHIPROCKET: ["ndr"],
}


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
    try:
        asyncio.run(_execute_sync(sync_job_id))
    finally:
        dispose_engine_sync()


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
    try:
        asyncio.run(_run_sync(integration_id, sync_type, entity_type))
    finally:
        dispose_engine_sync()


async def _run_scheduled_sync() -> list[tuple[str, str]]:
    enqueued: list[tuple[str, str]] = []
    async with AsyncSessionLocal() as session:
        integrations = (await session.execute(select(Integration))).scalars().all()
        for integration in integrations:
            entity_types = _SCHEDULED_SYNC_ENTITIES.get(integration.code)
            # Skip providers with no registered adapter (see the map above)
            # in-memory, with no network call — matches how
            # `SyncService.execute_sync` itself treats a missing adapter,
            # just done here before a SyncJob row is even created instead
            # of after, so an unimplemented provider never accumulates a
            # queue of guaranteed-FAILED jobs every cycle.
            if not entity_types or get_adapter(integration.code) is None:
                continue
            for entity_type in entity_types:
                run_sync_task.delay(str(integration.id), SyncType.INCREMENTAL.value, entity_type)
                enqueued.append((integration.code, entity_type))
    return enqueued


@celery_app.task(name="sync.run_scheduled")
def run_scheduled_sync_task() -> None:
    """Periodic entrypoint for Celery beat (see `beat_schedule` in
    `app.workers.celery_app`) — the automatic-sync backstop.

    Without this, an order placed on Shopify only ever reaches the OMS
    when a human clicks "Trigger Sync" on the Integrations page, or via a
    webhook (if one is registered on the Shopify side — see
    `app/api/v1/webhooks/shopify.py`, which is fully implemented but does
    nothing until Shopify is actually told to call it). Confirmed via a
    live reconciliation against the real store: with neither mechanism
    active, the OMS silently drifts further behind Shopify for as long as
    nobody manually syncs — this is what enqueues a small, cheap
    incremental sync (only orders/customers/products changed since the
    last successful sync — see `SyncService._run_entity_sync`) on a
    schedule, so that drift is bounded to one beat interval even if a
    webhook is missed, fails, or was never configured.
    """
    logger.info("scheduled_sync_started")
    try:
        enqueued = asyncio.run(_run_scheduled_sync())
        logger.info("scheduled_sync_enqueued", jobs=enqueued)
    finally:
        dispose_engine_sync()
