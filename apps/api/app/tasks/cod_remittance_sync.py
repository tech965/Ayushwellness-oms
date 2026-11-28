"""Celery task: scheduled Shiprocket COD remittance sync."""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.models.integration import IntegrationCode
from app.services.cod_reconciliation_service import CodReconciliationService
from app.workers.celery_app import SYNC_TASK_SOFT_TIME_LIMIT, SYNC_TASK_TIME_LIMIT, celery_app

logger = get_logger(__name__)


async def _run() -> None:
    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        logger.info("cod_remittance_sync_skipped_no_adapter")
        return
    async with AsyncSessionLocal() as session:
        try:
            await CodReconciliationService(session).sync_remittances(adapter)
        except Exception as exc:  # noqa: BLE001 - scheduled job must not crash the worker
            logger.warning("cod_remittance_sync_failed", error=str(exc))


@celery_app.task(
    name="shiprocket.sync_cod_remittances",
    soft_time_limit=SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=SYNC_TASK_TIME_LIMIT,
)
def sync_cod_remittances_task() -> None:
    asyncio.run(run_with_cleanup(_run()))
