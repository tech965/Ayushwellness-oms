"""SyncService — the only place that starts, tracks, and completes a
`SyncJob`, and the only place that updates `Integration` health fields as
a result of a sync (spec §12). Routes never touch this directly beyond
`run_sync`/`start_sync`; `app.tasks.sync_tasks` is the Celery entrypoint
that actually drives a job to completion off the request thread.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, IntegrationError, NotFoundError
from app.core.logging import get_logger
from app.integrations.base import IntegrationAdapter
from app.integrations.entity_sync import ENTITY_UPSERT_HANDLERS, UpsertHandler
from app.integrations.registry import get_adapter, registered_codes
from app.models.auth import User
from app.models.enums import IntegrationStatus, SyncJobStatus, SyncType
from app.models.integration import Integration, SyncError, SyncJob
from app.repositories.integration import IntegrationRepository
from app.repositories.sync_error import SyncErrorRepository
from app.repositories.sync_job import SyncJobRepository
from app.services.audit_service import AuditService

logger = get_logger(__name__)


class SyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.integrations = IntegrationRepository(session)
        self.sync_jobs = SyncJobRepository(session)
        self.sync_errors = SyncErrorRepository(session)
        self.audit = AuditService(session)

    async def _get_sync_job(self, sync_job_id: uuid.UUID) -> SyncJob:
        job = await self.sync_jobs.get_by_id(sync_job_id)
        if job is None:
            raise NotFoundError("Sync job not found.")
        return job

    async def start_sync(
        self,
        *,
        integration_id: uuid.UUID,
        sync_type: SyncType | str,
        entity_type: str,
        metadata: dict | None = None,
        actor: User | None = None,
    ) -> SyncJob:
        integration = await self.integrations.get_by_id(integration_id)
        if integration is None:
            raise NotFoundError("Integration not found.")

        existing = await self.sync_jobs.get_active_for_entity(
            integration_id=integration.id, entity_type=entity_type
        )
        if existing is not None:
            # Real production incident: nothing previously stopped the
            # scheduler (every 10 minutes) and manual/retry triggers from
            # all starting their own concurrent `shipments` sync -- 8 ended
            # up simultaneously "running" at once, several orphaned for
            # 18+ hours. One active job per (integration, entity_type) at
            # a time; see `run_sync` for how the scheduler path treats
            # this as "nothing to do" rather than an error.
            raise ConflictError(
                f"A {entity_type} sync is already {existing.status.value} for this " "integration.",
                details={"sync_job_id": str(existing.id), "status": existing.status.value},
            )

        job = await self.sync_jobs.create(
            integration_id=integration.id,
            sync_type=SyncType(sync_type),
            entity_type=entity_type,
            status=SyncJobStatus.QUEUED,
            job_metadata=metadata,
        )
        await self.audit.record(
            user=actor,
            action="sync.started",
            entity_type="sync_job",
            entity_id=str(job.id),
            new_value={
                "integration": integration.code,
                "sync_type": job.sync_type.value,
                "entity_type": entity_type,
            },
        )
        await self.session.commit()
        return job

    async def mark_running(self, sync_job_id: uuid.UUID) -> SyncJob:
        job = await self._get_sync_job(sync_job_id)
        await self.sync_jobs.update(job, status=SyncJobStatus.RUNNING, started_at=datetime.now(UTC))
        integration = await self.integrations.get_by_id(job.integration_id)
        if integration is not None:
            await self.integrations.update(integration, status=IntegrationStatus.SYNCING)
        await self.session.commit()
        return job

    async def record_progress(
        self,
        sync_job_id: uuid.UUID,
        *,
        received: int = 0,
        created: int = 0,
        updated: int = 0,
        skipped: int = 0,
        failed: int = 0,
    ) -> SyncJob:
        job = await self._get_sync_job(sync_job_id)
        await self.sync_jobs.update(
            job,
            records_received=job.records_received + received,
            records_created=job.records_created + created,
            records_updated=job.records_updated + updated,
            records_skipped=job.records_skipped + skipped,
            records_failed=job.records_failed + failed,
        )
        await self.session.commit()
        return job

    async def record_error(
        self,
        sync_job_id: uuid.UUID,
        *,
        entity_type: str,
        error_type: str,
        error_message: str,
        external_id: str | None = None,
        payload_reference: str | None = None,
    ) -> SyncError:
        job = await self._get_sync_job(sync_job_id)
        error = await self.sync_errors.create(
            sync_job_id=job.id,
            integration_id=job.integration_id,
            entity_type=entity_type,
            external_id=external_id,
            error_type=error_type,
            # error_message is String(1000) — an oversized message here
            # must never itself crash the sync (it already did once: a
            # long duplicate-key error message overflowed the column,
            # and *that* INSERT failure took down the whole Celery task
            # instead of just recording one bad record and moving on).
            error_message=error_message[:1000],
            payload_reference=payload_reference,
        )
        await self.sync_jobs.update(job, error_count=job.error_count + 1)
        await self.session.commit()
        return error

    async def complete_sync(self, sync_job_id: uuid.UUID, *, success: bool) -> SyncJob:
        job = await self._get_sync_job(sync_job_id)
        now = datetime.now(UTC)

        if not success:
            status = SyncJobStatus.FAILED
        elif job.error_count > 0:
            status = SyncJobStatus.PARTIAL
        else:
            status = SyncJobStatus.COMPLETED

        await self.sync_jobs.update(job, status=status, completed_at=now)

        logger.info(
            "sync_completed",
            sync_job_id=str(job.id),
            entity_type=job.entity_type,
            status=status.value,
            records_received=job.records_received,
            records_created=job.records_created,
            records_updated=job.records_updated,
            records_failed=job.records_failed,
            error_count=job.error_count,
        )

        integration = await self.integrations.get_by_id(job.integration_id)
        if integration is not None:
            await self._update_integration_health(integration, status=status, now=now)

        await self.audit.record(
            user=None,
            action="sync.completed" if status != SyncJobStatus.FAILED else "sync.failed",
            entity_type="sync_job",
            entity_id=str(job.id),
            new_value={
                "status": status.value,
                "records_received": job.records_received,
                "records_created": job.records_created,
                "records_updated": job.records_updated,
                "records_failed": job.records_failed,
                "error_count": job.error_count,
            },
        )
        await self.session.commit()
        return job

    async def cancel_sync(self, sync_job_id: uuid.UUID) -> SyncJob:
        job = await self._get_sync_job(sync_job_id)
        await self.sync_jobs.update(
            job, status=SyncJobStatus.CANCELLED, completed_at=datetime.now(UTC)
        )
        await self.session.commit()
        return job

    async def _update_integration_health(
        self, integration: Integration, *, status: SyncJobStatus, now: datetime
    ) -> None:
        if status in (SyncJobStatus.COMPLETED, SyncJobStatus.PARTIAL):
            await self.integrations.update(
                integration,
                status=IntegrationStatus.CONNECTED,
                last_sync_at=now,
                last_successful_sync_at=now,
            )
        else:
            await self.integrations.update(
                integration,
                status=IntegrationStatus.ERROR,
                last_sync_at=now,
                last_failure_at=now,
            )

    async def execute_sync(self, sync_job_id: uuid.UUID) -> SyncJob:
        """Runs an already-created (QUEUED) `SyncJob` to completion — the
        Celery-side half of the FastAPI -> SyncJob -> Celery -> Adapter
        pipeline (spec §9). Looks up the adapter for the job's integration;
        in Phase 2.1 the registry is always empty, so every sync fails
        gracefully with a single recorded `SyncError` instead of attempting
        any network call.

        `since` (the incremental-sync boundary) is derived per entity
        type from sync-job history, not from `Integration.
        last_successful_sync_at` — that field is one shared timestamp per
        *integration*, so for a multi-entity integration (Shopify: orders,
        customers, products) it only reflects whichever entity type
        happened to sync most recently. Reading it as "since" for every
        entity type meant that once any one entity type's sync completed
        and bumped it, the *next* entity type's incremental sync — even on
        its own first-ever run — saw a `since` of moments ago instead of
        "never", and fetched almost nothing. Confirmed live: a first-ever
        orders+customers+products sync against the real Shopify store
        correctly pulled every order (orders ran first, `since=None`), but
        customers and products (which ran immediately after, in the same
        cycle) each received 0 records, because orders' completion had
        already set `Integration.last_successful_sync_at` to "just now".
        """
        job = await self._get_sync_job(sync_job_id)
        job_id = job.id
        entity_type = job.entity_type
        sync_type = job.sync_type
        await self.mark_running(job_id)

        integration = await self.integrations.get_by_id(job.integration_id)
        adapter = get_adapter(integration.code) if integration else None
        integration_code = integration.code if integration else str(job.integration_id)

        # Temporary diagnostic (see the "No adapter registered" production
        # incident this is investigating): if `adapter` is ever `None` here
        # for a provider that's supposed to have one, this line is the
        # only way to tell *from Render logs alone* whether this process
        # ever ran adapter registration at all, or registered a different
        # set of providers than expected — `os.getpid()` also lets two log
        # lines be correlated to "the same process" or ruled out as
        # different ones, which a manual shell session can never prove
        # about a real worker process it isn't.
        logger.info(
            "sync_adapter_lookup",
            pid=os.getpid(),
            sync_job_id=str(job_id),
            integration=integration_code,
            entity_type=entity_type,
            adapter_found=adapter is not None,
            registered_adapters=registered_codes(),
        )
        last_job_for_entity = (
            await self.sync_jobs.get_last_successful_for_entity(
                integration_id=job.integration_id, entity_type=entity_type
            )
            if integration
            else None
        )
        since = last_job_for_entity.completed_at if last_job_for_entity else None

        # Round 5: sync_service.py had zero structured logging, making
        # "is the scheduled sync actually running against real data, or
        # just returning HTTP 200 with nothing to show for it" impossible
        # to answer from Render logs alone. Never logs the raw payload,
        # tokens, or PII -- just which entity/job/boundary is running.
        logger.info(
            "sync_started",
            sync_job_id=str(job_id),
            integration=integration_code,
            entity_type=entity_type,
            sync_type=sync_type.value,
            since=since.isoformat() if since else None,
        )

        if adapter is None:
            await self.record_error(
                job_id,
                entity_type=entity_type,
                error_type="integration_error",
                error_message=f"No adapter registered for integration '{integration_code}'.",
            )
            return await self.complete_sync(job_id, success=False)

        handler = ENTITY_UPSERT_HANDLERS.get(entity_type)
        if handler is None:
            await self.record_error(
                job_id,
                entity_type=entity_type,
                error_type="validation_error",
                error_message=f"No sync handler registered for entity_type '{entity_type}'.",
            )
            return await self.complete_sync(job_id, success=False)

        try:
            await self._run_entity_sync(
                job_id=job_id,
                entity_type=entity_type,
                sync_type=sync_type,
                since=since,
                adapter=adapter,
                handler=handler,
            )
        except IntegrationError as exc:
            # A page-level failure (auth/permission/rate-limit/network) —
            # not a single bad record, which is instead caught inside
            # `_run_entity_sync` and recorded without aborting the job.
            await self.record_error(
                job_id,
                entity_type=entity_type,
                error_type=exc.details.get("error_type", "integration_error"),
                error_message=exc.message,
            )
            return await self.complete_sync(job_id, success=False)

        return await self.complete_sync(job_id, success=True)

    async def _run_entity_sync(
        self,
        *,
        job_id: uuid.UUID,
        entity_type: str,
        sync_type: SyncType,
        since: datetime | None,
        adapter: IntegrationAdapter,
        handler: UpsertHandler,
    ) -> None:
        """Pages through every record of `entity_type`, normalizing and
        upserting each one via `handler`. A single record's failure is
        caught, rolled back, and recorded as a `SyncError` — it never
        aborts the rest of the page or the job (spec §23: 998/1000 succeeding
        makes the job `PARTIAL`, not `FAILED`). A failure fetching a page
        itself (auth, permission, rate limit, network) propagates to the
        caller, which records one `SyncError` and fails the whole job —
        there's no partial data to salvage from a page that never arrived.

        Every identifier this loop needs (`job_id`, `entity_type`,
        `sync_type`, `since`) is passed in as a plain value rather than
        read off the `SyncJob`/`Integration` ORM objects — `session.rollback()`
        after a failed record expires every attribute on every object
        still attached to this session, and a later plain attribute
        access (not an `await`ed reload) on an expired attribute raises
        `MissingGreenlet` under `AsyncSession`.
        """
        cursor: str | None = None
        while True:
            if sync_type == SyncType.INCREMENTAL and since:
                page = await adapter.fetch_incremental(
                    entity_type, since=since, cursor=cursor, limit=50
                )
            else:
                page = await adapter.fetch(entity_type, cursor=cursor, limit=50)

            created_count = 0
            updated_count = 0
            failed_count = 0

            for raw in page.nodes:
                external_id = raw.get("id")
                try:
                    normalized = adapter.normalize(entity_type, raw)
                    external_id = normalized.get("external_id", external_id)
                    _, created = await handler(self.session, normalized)
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                except IntegrationError as exc:
                    # A per-record failure that couldn't even complete
                    # (auth/permission/network/timeout on a fallback live
                    # call, e.g. Shiprocket's `/orders/show`) — kept
                    # distinct from a genuine "checked, no match"
                    # `NotFoundError` (which falls through to the generic
                    # branch below as "validation_error") so a caller
                    # (e.g. `entity_sync._upsert_shipment`'s "already
                    # confirmed, don't recheck" cache) can tell "we
                    # couldn't check" apart from "we checked and there's
                    # nothing there" — the former must always be retried,
                    # the latter safely never needs to be re-attempted.
                    await self.session.rollback()
                    failed_count += 1
                    await self.record_error(
                        job_id,
                        entity_type=entity_type,
                        external_id=str(external_id) if external_id else None,
                        error_type=exc.details.get("error_type", "integration_error"),
                        error_message=exc.message,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad record must not kill the job
                    await self.session.rollback()
                    failed_count += 1
                    await self.record_error(
                        job_id,
                        entity_type=entity_type,
                        external_id=str(external_id) if external_id else None,
                        error_type="validation_error",
                        error_message=str(exc),
                    )

            await self.record_progress(
                job_id,
                received=len(page.nodes),
                created=created_count,
                updated=updated_count,
                failed=failed_count,
            )
            # A Shopify GraphQL call returning HTTP 200 only proves the
            # request succeeded -- it says nothing about how many nodes
            # came back or what happened when upserting them. Log the
            # real per-page outcome so a Render log line can answer that
            # without needing DB access.
            logger.info(
                "sync_page_processed",
                sync_job_id=str(job_id),
                entity_type=entity_type,
                nodes_received=len(page.nodes),
                created=created_count,
                updated=updated_count,
                failed=failed_count,
                has_more=page.has_more,
            )

            if not page.has_more:
                break
            cursor = page.next_cursor

    async def run_sync(
        self, *, integration_id: uuid.UUID, sync_type: SyncType | str, entity_type: str
    ) -> SyncJob:
        """Convenience wrapper: create a new `SyncJob` and run it
        end-to-end in one call. Used by `app.tasks.retry_processing` (which
        starts a fresh attempt rather than reusing the failed job's id),
        the scheduled-sync backstop, and by tests that don't need the
        intermediate QUEUED state.

        A `ConflictError` from `start_sync` (a sync for this entity type
        is already active) is treated as "nothing to do" here rather than
        an error — the scheduler calling this every 10 minutes must never
        pile up a second concurrent crawl on top of one already running;
        it returns the already-active job untouched instead.
        """
        try:
            job = await self.start_sync(
                integration_id=integration_id, sync_type=sync_type, entity_type=entity_type
            )
        except ConflictError:
            existing = await self.sync_jobs.get_active_for_entity(
                integration_id=integration_id, entity_type=entity_type
            )
            if existing is None:
                # Vanishingly rare race: the conflicting job finished
                # between start_sync's check and this lookup -- safe to
                # just try again rather than return None.
                return await self.run_sync(
                    integration_id=integration_id, sync_type=sync_type, entity_type=entity_type
                )
            logger.info(
                "sync_run_skipped_already_active",
                integration_id=str(integration_id),
                entity_type=entity_type,
                existing_sync_job_id=str(existing.id),
                existing_status=existing.status.value,
            )
            return existing
        return await self.execute_sync(job.id)
