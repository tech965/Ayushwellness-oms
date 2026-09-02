"""Celery tasks driving `SyncService` off the request thread (spec §9's
FastAPI -> SyncJob -> Celery -> Adapter pipeline).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.integrations.registry import get_adapter
from app.models.enums import SyncType
from app.models.integration import Integration, IntegrationCode
from app.repositories.sync_job import SyncJobRepository
from app.services.sync_service import SyncService
from app.workers.celery_app import (
    SYNC_TASK_SOFT_TIME_LIMIT,
    SYNC_TASK_TIME_LIMIT,
    celery_app,
)

logger = get_logger(__name__)

# A RUNNING job's `updated_at` is bumped by every `record_progress`/
# `record_error` call -- a real heartbeat, not just elapsed wall-clock
# time, so a genuinely slow multi-hour crawl (a large Shiprocket
# historical backlog) is never mistaken for a dead one; real production
# evidence this engagement showed pages updating every 1-2 seconds during
# normal operation. 20 minutes of silence comfortably exceeds even the
# longest observed per-page pause (a 60s rate-limit backoff).
STALE_SYNC_JOB_THRESHOLD = timedelta(minutes=20)

# Which entity types each provider's adapter actually supports syncing
# generically (matches what `ShopifyAdapter`/`ShiprocketAdapter.fetch()`
# accept — tracking refresh runs through its own dedicated task, not
# this path). A provider with no adapter registered (Blue Dart/Delhivery/
# Ecom Express/WhatsApp/Meta/Instagram — no real adapter exists for any
# of them yet) is intentionally absent here, not just filtered out at
# runtime, so adding one is a one-line change instead of a silent
# behavior change to what already runs.
#
# "shipments" is listed before "ndr" deliberately: an NDR is only
# matchable to an OMS shipment once that shipment has actually been
# pulled in (see `app.integrations.entity_sync._upsert_shipment` — a
# real, previously-diagnosed production incident: 102/102 real NDR
# records failed with "No OMS shipment found" because nothing had ever
# imported Shiprocket's existing shipments, only the reverse push flow
# from `ShiprocketOperationsService.create_shipment_for_order` created
# any). Each entity type still runs as its own separate `SyncJob`/task,
# so this ordering is a same-cycle best-effort, not a hard transactional
# guarantee — an NDR for a shipment that arrives moments later in the
# same store will simply be picked up on the next scheduled cycle.
_SCHEDULED_SYNC_ENTITIES: dict[str, list[str]] = {
    IntegrationCode.SHOPIFY: ["orders", "customers", "products"],
    IntegrationCode.SHIPROCKET: ["shipments", "ndr"],
}


async def _execute_sync(sync_job_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await SyncService(session).execute_sync(uuid.UUID(sync_job_id))


@celery_app.task(
    name="sync.execute",
    soft_time_limit=SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=SYNC_TASK_TIME_LIMIT,
)
def execute_sync_task(sync_job_id: str) -> None:
    """Runs an already-created (QUEUED) `SyncJob`. This is what the manual
    trigger endpoint (`POST /sync/{integration_id}/trigger`) enqueues,
    since it already created the job synchronously to return its id.
    """
    logger.info("sync_task_started", sync_job_id=sync_job_id)
    asyncio.run(run_with_cleanup(_execute_sync(sync_job_id)))


async def _run_sync(integration_id: str, sync_type: str, entity_type: str) -> None:
    async with AsyncSessionLocal() as session:
        await SyncService(session).run_sync(
            integration_id=uuid.UUID(integration_id),
            sync_type=sync_type,
            entity_type=entity_type,
        )


@celery_app.task(
    name="sync.run",
    soft_time_limit=SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=SYNC_TASK_TIME_LIMIT,
)
def run_sync_task(integration_id: str, sync_type: str, entity_type: str) -> None:
    """Creates and runs a new `SyncJob` in one step — for callers (Celery
    beat, `app.tasks.retry_processing`) that don't already have a job id.
    """
    logger.info("sync_task_started", integration_id=integration_id, entity_type=entity_type)
    asyncio.run(run_with_cleanup(_run_sync(integration_id, sync_type, entity_type)))


async def _run_scheduled_sync() -> list[tuple[str, str]]:
    """Real production incident this fixes: this loop used to hand every
    entity `SyncType.INCREMENTAL.value` unconditionally, on every single
    10-minute cycle, including the very first one an entity ever saw.
    `SyncService._run_entity_sync`'s own `since`/`resume_cursor` checks
    already stopped that from *executing* as a real incremental fetch
    before a backlog baseline existed — but the *label* on every one of
    those `SyncJob` rows still read "incremental", which is exactly what
    made a real gap (Shopify orders older than ~2026-03 never imported)
    look, from Sync History and Render logs alone, like ordinary
    incremental runs turning up nothing new rather than an interrupted or
    never-attempted backlog crawl. Each entity now gets `SyncType.FULL`
    until its own `backlog_complete` flag is genuinely set (see
    `SyncService.backlog_known_complete_for` / `_mark_backlog_complete`) —
    `_run_entity_sync` already resumes a `FULL` request from any
    persisted cursor rather than restarting at page 1, so this changes
    nothing about execution for an entity whose backlog is already done
    or already in progress; it only fixes what gets recorded.
    """
    enqueued: list[tuple[str, str]] = []
    async with AsyncSessionLocal() as session:
        integrations = (await session.execute(select(Integration))).scalars().all()
        sync_jobs = SyncJobRepository(session)
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
                last_successful = await sync_jobs.get_last_successful_for_entity(
                    integration_id=integration.id, entity_type=entity_type
                )
                since = last_successful.completed_at if last_successful else None
                resume_cursor = (
                    (integration.configuration or {}).get("sync_cursors", {}).get(entity_type)
                )
                backlog_done = SyncService.backlog_known_complete_for(
                    integration, entity_type=entity_type, since=since, resume_cursor=resume_cursor
                )
                sync_type = (
                    SyncType.INCREMENTAL.value if backlog_done else SyncType.FULL.value
                )
                run_sync_task.delay(str(integration.id), sync_type, entity_type)
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
    enqueued = asyncio.run(run_with_cleanup(_run_scheduled_sync()))
    logger.info("scheduled_sync_enqueued", jobs=enqueued)


async def _reap_stale_sync_jobs() -> list[str]:
    reaped: list[str] = []
    async with AsyncSessionLocal() as session:
        cutoff = datetime.now(UTC) - STALE_SYNC_JOB_THRESHOLD
        repo = SyncJobRepository(session)
        stale_running = await repo.get_stale_running(updated_before=cutoff)
        # A QUEUED job that no worker ever picked up is just as capable of
        # wedging every future scheduled sync for its entity type (via
        # `start_sync`'s one-active-job guard, which counts QUEUED as
        # active) as a stuck RUNNING one — and nothing else ever clears
        # it. Reap both in the same pass.
        stale_queued = await repo.get_stale_queued(created_before=cutoff)
        sync_service = SyncService(session)
        threshold_minutes = int(STALE_SYNC_JOB_THRESHOLD.total_seconds() // 60)
        for job in stale_running:
            await sync_service.record_error(
                job.id,
                entity_type=job.entity_type,
                error_type="orphaned",
                error_message=(
                    "Sync job marked failed by the stale-job reaper: no progress for "
                    f"over {threshold_minutes} minutes "
                    "(the worker process most likely restarted mid-sync, e.g. during a "
                    "deploy, and never got to mark this job complete)."
                ),
            )
            await sync_service.complete_sync(job.id, success=False)
            reaped.append(str(job.id))
        for job in stale_queued:
            await sync_service.record_error(
                job.id,
                entity_type=job.entity_type,
                error_type="orphaned",
                error_message=(
                    "Sync job marked failed by the stale-job reaper: still QUEUED "
                    f"over {threshold_minutes} minutes after creation "
                    "(no worker ever started it — a lost broker message, a worker "
                    "killed in the QUEUED->RUNNING window, or an enqueue that failed "
                    "on a broker outage). Left as-is it would block every later "
                    "scheduled sync for this entity type via the one-active-job guard."
                ),
            )
            await sync_service.complete_sync(job.id, success=False)
            reaped.append(str(job.id))
    return reaped


@celery_app.task(name="sync.reap_stale")
def reap_stale_sync_jobs_task() -> list[str]:
    """Scheduled backstop (Celery Beat, see `beat_schedule` in
    `app.workers.celery_app`) for a `SyncJob` orphaned by its worker
    process dying mid-sync — real production incident: 8 separate
    `shipments` sync jobs were stuck `RUNNING`, several for 18+ hours,
    after worker restarts (deploys) killed them mid-flight with no way
    for anything to ever mark them complete. Combined with `start_sync`'s
    new one-active-job-per-entity-type guard, this both prevents new
    duplicates and cleans up whatever's already stuck.
    """
    logger.info("reap_stale_sync_jobs_started")
    reaped = asyncio.run(run_with_cleanup(_reap_stale_sync_jobs()))
    if reaped:
        logger.warning("stale_sync_jobs_reaped", count=len(reaped), job_ids=reaped)
    return reaped
