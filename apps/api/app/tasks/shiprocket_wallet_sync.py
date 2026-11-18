"""Celery tasks: scheduled Shiprocket wallet balance refresh and (opt-in)
wallet statement sync. Failures are logged, never raised into the worker."""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.models.integration import IntegrationCode
from app.services.shiprocket_wallet_service import WalletService
from app.workers.celery_app import SYNC_TASK_SOFT_TIME_LIMIT, SYNC_TASK_TIME_LIMIT, celery_app

logger = get_logger(__name__)


async def _balance() -> None:
    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        logger.info("wallet_balance_refresh_skipped_no_adapter")
        return
    async with AsyncSessionLocal() as session:
        try:
            await WalletService(session).refresh_balance(adapter, trigger="scheduled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("wallet_balance_refresh_failed", error=str(exc))


async def _ledger() -> None:
    if not settings.SHIPROCKET_WALLET_STATEMENT_PATH:
        logger.info("wallet_ledger_sync_disabled_no_path")
        return
    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        return
    async with AsyncSessionLocal() as session:
        try:
            await WalletService(session).sync_transactions(adapter)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wallet_ledger_sync_failed", error=str(exc))


@celery_app.task(
    name="shiprocket.refresh_wallet_balance",
    soft_time_limit=SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=SYNC_TASK_TIME_LIMIT,
)
def refresh_wallet_balance_task() -> None:
    asyncio.run(run_with_cleanup(_balance()))


@celery_app.task(
    name="shiprocket.sync_wallet_ledger",
    soft_time_limit=SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=SYNC_TASK_TIME_LIMIT,
)
def sync_wallet_ledger_task() -> None:
    asyncio.run(run_with_cleanup(_ledger()))
