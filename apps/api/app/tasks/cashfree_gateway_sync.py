"""Celery tasks: Cashfree settlement / transaction synchronisation (Module 7).

* `cashfree.sync_settlements` -- scheduled; a no-op without Cashfree
  credentials or when disabled by `CASHFREE_SYNC_ENABLED`.
* `cashfree.sync_manual` -- "Sync Now", queued by the API. Retries while
  another sync holds the lock. The outcome is recorded in the sync state.

Neither raises a sync failure into the worker. Both are safe to run again:
the sync is idempotent and takes a database lock.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.models.auth import User
from app.services.cashfree_gateway_sync_service import CashfreeGatewaySyncService
from app.workers.celery_app import SYNC_TASK_SOFT_TIME_LIMIT, SYNC_TASK_TIME_LIMIT, celery_app

logger = get_logger(__name__)

BUSY_RETRY_SECONDS = 60
BUSY_MAX_RETRIES = 5


async def _run() -> None:
    if not settings.CASHFREE_SYNC_ENABLED:
        logger.info("cashfree_gateway_sync_disabled")
        return
    async with AsyncSessionLocal() as session:
        try:
            result = await CashfreeGatewaySyncService(session).sync(trigger="scheduled")
            logger.info("cashfree_gateway_sync_task_done", status=result.get("status"))
        except Exception as exc:  # noqa: BLE001 - scheduled job must not crash the worker
            logger.warning("cashfree_gateway_sync_task_failed", error=type(exc).__name__)


@celery_app.task(
    name="cashfree.sync_settlements",
    soft_time_limit=SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=SYNC_TASK_TIME_LIMIT,
)
def sync_cashfree_settlements_task() -> None:
    asyncio.run(run_with_cleanup(_run()))


async def _run_manual(user_id: str | None, date_from: str | None, date_to: str | None) -> str:
    async with AsyncSessionLocal() as session:
        try:
            user = await session.get(User, uuid.UUID(user_id)) if user_id else None
            result = await CashfreeGatewaySyncService(session).sync(
                trigger="manual",
                date_from=datetime.fromisoformat(date_from) if date_from else None,
                date_to=datetime.fromisoformat(date_to) if date_to else None,
                user=user,
            )
            logger.info("cashfree_manual_sync_task_done", status=result.get("status"))
            return str(result.get("status"))
        except Exception as exc:  # noqa: BLE001 - failure is recorded in the sync state
            await session.rollback()
            logger.error("cashfree_manual_sync_failed", error=type(exc).__name__)
            try:
                await CashfreeGatewaySyncService(session).release_after_crash(type(exc).__name__)
            except Exception:  # noqa: BLE001
                await session.rollback()
            return "failed"


@celery_app.task(
    name="cashfree.sync_manual",
    bind=True,
    max_retries=BUSY_MAX_RETRIES,
    soft_time_limit=SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=SYNC_TASK_TIME_LIMIT,
)
def sync_cashfree_manual_task(
    self: Any, user_id: str | None, date_from: str | None = None, date_to: str | None = None
) -> None:
    status = asyncio.run(run_with_cleanup(_run_manual(user_id, date_from, date_to)))
    if status == "already_running" and self.request.retries < self.max_retries:
        raise self.retry(countdown=BUSY_RETRY_SECONDS)
