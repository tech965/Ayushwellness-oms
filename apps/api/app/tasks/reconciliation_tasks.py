"""Celery task: run a reconciliation pass.

Never runs inside a FastAPI request handler (spec §11/§27 — reconciliation
calls live provider APIs, the same "never long-running work on the
request thread" rule every sync task already follows). Mirrors
`app.tasks.shiprocket_sync`'s structure: open a fresh session, drive an
already-created (`RUNNING`) run to completion, and never let an
unexpected failure leave the run silently stuck.
"""

from __future__ import annotations

import asyncio
import uuid

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.services.reconciliation_service import ReconciliationService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _execute_reconciliation_run(run_id: str) -> None:
    async with AsyncSessionLocal() as session:
        service = ReconciliationService(session)
        run_uuid = uuid.UUID(run_id)
        try:
            await service.run_checks(run_uuid)
        except Exception as exc:  # noqa: BLE001 - a run must never be left stuck as RUNNING
            logger.error("reconciliation_run_failed", run_id=run_id, error=str(exc))
            await session.rollback()
            await service.fail_run(run_uuid, message=str(exc)[:1000])


@celery_app.task(name="reconciliation.run")
def run_reconciliation_task(run_id: str) -> None:
    logger.info("reconciliation_run_started", run_id=run_id)
    asyncio.run(run_with_cleanup(_execute_reconciliation_run(run_id)))
