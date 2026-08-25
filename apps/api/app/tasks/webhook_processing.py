"""Celery task: process one `WebhookEvent`.

Looks up the registered adapter for the event's integration and calls
`adapter.process_webhook()`, which returns a normalized entity (never
touches the database itself — see
`docs/architecture/integrations.md#why-the-oms-core-must-not-import-a-provider-sdk`).
This task is what actually persists it, via the same
`ENTITY_UPSERT_HANDLERS` dispatch table `SyncService` uses, so a webhook
and a pull-based sync converge on the identical OMS service call. With
no adapter registered, every event is marked IGNORED rather than
PROCESSED; no network call is ever made from this module.
"""

from __future__ import annotations

import asyncio
import uuid

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.integrations.entity_sync import ENTITY_UPSERT_HANDLERS
from app.integrations.registry import get_adapter
from app.repositories.integration import IntegrationRepository
from app.services.webhook_service import WebhookService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _process_webhook_event(webhook_event_id: str) -> None:
    async with AsyncSessionLocal() as session:
        service = WebhookService(session)
        event = await service.get_event(uuid.UUID(webhook_event_id))
        await service.mark_processing(event.id)

        integration = await IntegrationRepository(session).get_by_id(event.integration_id)
        adapter = get_adapter(integration.code) if integration else None

        if adapter is None:
            await service.mark_ignored(
                event.id,
                reason=(
                    "No adapter registered for integration "
                    f"'{integration.code if integration else event.integration_id}'."
                ),
            )
            return

        try:
            result = await adapter.process_webhook(event.event_type, event.payload)
            entity_type = result.get("entity_type")
            normalized = result.get("normalized")
            handler = ENTITY_UPSERT_HANDLERS.get(entity_type) if entity_type else None

            if handler is None or normalized is None:
                await service.mark_ignored(
                    event.id, reason=f"Unhandled webhook topic '{event.event_type}'."
                )
                return

            await handler(session, normalized)
        except Exception as exc:  # noqa: BLE001 - persisted before re-raising for Celery's retry
            await session.rollback()
            await service.mark_failed(event.id, error_message=str(exc))
            raise
        else:
            await service.mark_processed(event.id)


@celery_app.task(name="webhooks.process_event", bind=True, max_retries=5)
def process_webhook_event_task(self, webhook_event_id: str) -> None:
    logger.info("webhook_event_task_started", webhook_event_id=webhook_event_id)
    try:
        asyncio.run(_process_webhook_event(webhook_event_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook_event_task_failed", webhook_event_id=webhook_event_id, error=str(exc)
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc
