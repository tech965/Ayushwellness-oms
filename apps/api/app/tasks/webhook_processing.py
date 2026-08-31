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
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.integrations.entity_sync import ENTITY_UPSERT_HANDLERS
from app.integrations.registry import get_adapter
from app.repositories.integration import IntegrationRepository
from app.repositories.webhook_event import WebhookEventRepository
from app.services.webhook_service import WebhookService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# How long a WebhookEvent may legitimately sit at RECEIVED waiting for
# its worker before `recover_stuck_webhook_events_task` treats it as
# abandoned (its Celery enqueue call failed — see that task's docstring)
# rather than just slow.
STUCK_WEBHOOK_EVENT_THRESHOLD = timedelta(minutes=5)


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
        asyncio.run(run_with_cleanup(_process_webhook_event(webhook_event_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook_event_task_failed", webhook_event_id=webhook_event_id, error=str(exc)
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


async def _recover_stuck_webhook_events() -> list[str]:
    recovered: list[str] = []
    async with AsyncSessionLocal() as session:
        cutoff = datetime.now(UTC) - STUCK_WEBHOOK_EVENT_THRESHOLD
        stuck = await WebhookEventRepository(session).get_stuck_received(received_before=cutoff)
        for event in stuck:
            recovered.append(str(event.id))
    # Enqueue outside the session above — `.delay()` makes a network call
    # to the broker and shouldn't hold a DB transaction open while it does.
    for event_id in recovered:
        process_webhook_event_task.delay(event_id)
    return recovered


@celery_app.task(name="webhooks.recover_stuck")
def recover_stuck_webhook_events_task() -> list[str]:
    """Scheduled backstop (Celery Beat, see `celery_app.py`) for the one
    way a `WebhookEvent` can be silently lost: `receive_shopify_webhook`
    persists it before calling `process_webhook_event_task.delay()`, and
    deliberately doesn't fail the webhook ack if that enqueue call itself
    raises (a broker outage, e.g. Redis unreachable at the exact moment
    the webhook arrived) — so nothing was re-driving that event once the
    broker recovered. This re-enqueues anything still sitting at
    RECEIVED after `STUCK_WEBHOOK_EVENT_THRESHOLD`. A worker being briefly
    down (as opposed to the broker) doesn't need this: Celery/Redis
    itself retains a successfully-enqueued message until a worker is
    available, so that case already recovers with no extra code.
    """
    recovered = asyncio.run(run_with_cleanup(_recover_stuck_webhook_events()))
    if recovered:
        logger.warning("stuck_webhook_events_recovered", count=len(recovered), event_ids=recovered)
    return recovered
